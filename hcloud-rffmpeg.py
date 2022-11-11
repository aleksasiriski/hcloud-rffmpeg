import os
import sys
import logging
import asyncio

import re
from typing import Pattern

from contextlib import contextmanager
from pathlib import Path
from sqlite3 import connect as sqlite_connect
from subprocess import run

from hcloud import Client
from hcloud.server_types.domain import ServerType
from hcloud.images.domain import Image
from hcloud.placement_groups.domain import PlacementGroup
from hcloud.locations.domain import Location


log = logging.getLogger("rffmpeg")

def setup_logger(config):

    if os.getenv("DEBUG") == None:
        logging_level = logging.INFO
    else:
        logging_level = logging.DEBUG

    logging.basicConfig(
        filename=config["log_file"],
        level=logging_level,
        format="%(asctime)s - %(name)s[%(process)s] - %(levelname)s - %(message)s",
    )

def fail(msg):
    print(msg)
    exit(1)

def setup():
    config = dict()


    STATE_DIR = os.getenv("STATE_DIR")
    if STATE_DIR == None:
        STATE_DIR = "/config/rffmpeg"
    config["state_dir"] = STATE_DIR

    LOG_FILE = os.getenv("LOG_FILE")
    if LOG_FILE == None:
        LOG_FILE = STATE_DIR + "/hcloud-rffmpeg.log"
    config["log_file"] = LOG_FILE

    DB_PATH = os.getenv("DB_PATH")
    if DB_PATH == None:
        DB_PATH = STATE_DIR + "/rffmpeg.db"
    config["db_path"] = DB_PATH

    KNOWN_HOSTS = os.getenv("KNOWN_HOSTS")
    if KNOWN_HOSTS == None:
        KNOWN_HOSTS = STATE_DIR + "/.ssh/known_hosts"
    config["known_hosts"] = KNOWN_HOSTS

    ID_RSA_PUB = os.getenv("ID_RSA_PUB")
    if ID_RSA_PUB == None:
        ID_RSA_PUB = STATE_DIR + "/.ssh/id_rsa.pub"
    config["id_rsa_pub"] = ID_RSA_PUB


    RFFMPEG_LAN_IP = os.getenv("RFFMPEG_LAN_IP")
    if RFFMPEG_LAN_IP == None:
        fail("RFFMPEG_LAN_IP env isn't set")
    config["rffmpeg_lan_ip"] = RFFMPEG_LAN_IP

    HCLOUD_API_TOKEN = os.getenv("HCLOUD_API_TOKEN")
    if HCLOUD_API_TOKEN == None:
        fail("HCLOUD_API_TOKEN env isn't set")
    config["hcloud_api_token"] = HCLOUD_API_TOKEN

    CIFS_MEDIA_USERNAME = os.getenv("CIFS_MEDIA_USERNAME")
    if CIFS_MEDIA_USERNAME == None:
        fail("CIFS_MEDIA_USERNAME env isn't set")
    config["cifs_media_username"] = CIFS_MEDIA_USERNAME

    CIFS_MEDIA_PASSWORD = os.getenv("CIFS_MEDIA_PASSWORD")
    if CIFS_MEDIA_PASSWORD == None:
        fail("CIFS_MEDIA_PASSWORD env isn't set")
    config["cifs_media_password"] = CIFS_MEDIA_PASSWORD


    config["client"] = Client(token=HCLOUD_API_TOKEN)

    SERVER_TYPE = os.getenv("SERVER_TYPE")
    if SERVER_TYPE == None:
        SERVER_TYPE = "cx21"
    config["server_type"] = ServerType(name=SERVER_TYPE)

    IMAGE = os.getenv("IMAGE")
    if IMAGE == None:
        IMAGE = "rocky-9"
    config["image"] = Image(name=IMAGE)


    SSH_KEY_NAME = os.getenv("SSH_KEY_NAME")
    if SSH_KEY_NAME == None:
        SSH_KEY_NAME = "root@jellyfin"

    ssh_key = config["client"].ssh_keys.get_by_name(name=SSH_KEY_NAME)
    try:
        config["client"].ssh_keys.delete(ssh_key)
        log.debug("Found key and removed it.")
    except:
        log.debug("No key found to remove.")

    public_key = ""
    with open(config["id_rsa_pub"], 'r') as id_rsa_pub_file:
        public_key = id_rsa_pub_file.readline()
        id_rsa_pub_file.close()

    config["client"].ssh_keys.create(
        name=SSH_KEY_NAME,
        public_key=public_key
    )

    config["ssh_keys"] = config["client"].ssh_keys.get_all(name=SSH_KEY_NAME)


    NETWORKS = os.getenv("NETWORKS")
    if NETWORKS == None:
        NETWORKS = "rffmpeg"
    config["networks"] = config["client"].networks.get_all(name=NETWORKS)

    FIREWALLS = os.getenv("FIREWALLS")
    if FIREWALLS == None:
        FIREWALLS = "rffmpeg"
    config["firewalls"] = config["client"].firewalls.get_all(name=FIREWALLS)

    PLACEMENT_GROUP = os.getenv("PLACEMENT_GROUP")
    if PLACEMENT_GROUP == None:
        PLACEMENT_GROUP = "rffmpeg"
    config["placement_group"] = config["client"].placement_groups.get_by_name(name=PLACEMENT_GROUP)

    LOCATION = os.getenv("LOCATION")
    if LOCATION == None:
        LOCATION = "nbg1"
    config["location"] = Location(name=LOCATION)

    CLOUD_CONFIG = os.getenv("CLOUD_CONFIG")
    if CLOUD_CONFIG == None:
        CLOUD_CONFIG = "#cloud-config\nruncmd:\n- systemctl disable --now sshd.service\n- echo 'RFFMPEG_LAN_IP=%s' | tee -a /root/.env\n- echo 'CIFS_MEDIA_USERNAME=%s' | tee -a /root/.env\n- echo 'CIFS_MEDIA_PASSWORD=%s' | tee -a /root/.env\n- fallocate -l 4G /swapfile\n- chmod 600 /swapfile\n- mkswap /swapfile\n- swapon /swapfile\n- echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab\n- dnf config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo\n- dnf update -y --security --bugfix\n- dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin\n- wget https://raw.githubusercontent.com/aleksasiriski/jellyfin-rffmpeg-node/latest/daemon.json -O /etc/docker/daemon.json\n- systemctl enable --now docker.service\n- wget https://raw.githubusercontent.com/aleksasiriski/jellyfin-rffmpeg-node/latest/docker-compose.example.yml -O /root/docker-compose.yml\n- cd /root && docker compose pull && docker compose up -d\n"%(RFFMPEG_LAN_IP, CIFS_MEDIA_USERNAME, CIFS_MEDIA_PASSWORD)
    config["cloud_config"] = CLOUD_CONFIG


    config["recently_made_node_bool"] = False


    return config

@contextmanager
def dbconn(config):
    conn = sqlite_connect(config["db_path"])
    conn.execute("PRAGMA foreign_keys = 1")
    cur = conn.cursor()
    yield cur
    conn.commit()
    conn.close()

def run_command(command):
    return run(command, shell = True)


async def recently_made_node_timer(config, delay):
    await asyncio.sleep(delay)
    config["recently_made_node_bool"] = False
    log.debug("Timer finished, able to make nodes again!")

async def recently_made_node(config):
    if config["recently_made_node_bool"]:
        return True
    else:
        config["recently_made_node_bool"] = True
        asyncio.create_task(recently_made_node_timer(config, 180))
        return False

async def next_node_name(config):
    log.debug("Generating name for the next node.")
        
    with dbconn(config) as cur:
        hosts = cur.execute("SELECT * FROM hosts").fetchall()

    name = "rffmpegnode"
    next_number = 1

    if len(hosts) < 1:
        log.debug("No nodes found.")
    else:
        log.debug("Using largest node number + 1.")
        for host in hosts:
            hid, hostname, weight, server_name = host
            node_number = server_name.split(name, 1)[1]
            if next_number <= node_number:
                next_number = node_number + 1
    
    new_name = name + str(next_number)
    log.debug("New name is " + new_name)

    return new_name

async def keyscan_node(config, server_name, server_ip):
    log.debug("Keyscanning node %s with IP %s"%(server_name, server_ip))

    header = "echo '#begin %s' | tee -a "%(server_name) + config["known_hosts"]
    footer = "echo '#end %s' | tee -a "%(server_name) + config["known_hosts"]

    ssh_keyscan = "ssh-keyscan " + server_ip + " | tee -a " + config["known_hosts"]

    return run_command(header + " && " + ssh_keyscan + " && " + footer)

async def create_server(config):
    log.info("Creating a server!")

    if not await recently_made_node(config):
        log.debug("No recently made servers!")

        server_name = await next_node_name(config)
        response = config["client"].servers.create(
            name=server_name,
            server_type=config["server_type"],
            image=config["image"],
            ssh_keys=config["ssh_keys"],
            networks=config["networks"],
            firewalls=config["firewalls"],
            placement_group=config["placement_group"],
            location=config["location"],
            user_data=config["cloud_config"]
        )

        if response.action.status == "error":
            log.error("Error occured while creating the server in HCloud!")
        else:
            await asyncio.sleep(60)
            log.debug("Successfully created a server in HCloud!")

            server_ip = config["client"].servers.get_by_name(name=server_name).private_net[0].ip
            await keyscan_node(config, server_name, server_ip)

            weight = 1
            with dbconn(config) as cur:
                cur.execute("INSERT INTO hosts (hostname, weight, server_name) VALUES (?, ?, ?)", (server_ip, weight, server_name))

            log.info("Added %s with IP %s to database!"%(server_name,server_ip))
            asyncio.create_task(check_unused_node(config, server_name))

    else:
        log.debug("Recently made a server!")

async def remove_keyscan(config, server_name):
    log.debug("Removing keyscanned node")

    if os.path.exists(config["known_hosts"]):
        log.debug("Found known hosts file.")

        header = "#begin %s"%(server_name)
        footer = "#end %s"%(server_name)
        known_hosts = ""

        with open(config["known_hosts"], 'r') as known_hosts_file:
            lines = known_hosts_file.readlines()

            found_header = False
            found_footer = False

            for line in lines:
                if not found_header and line == header:
                    found_header = True
                elif not found_footer and line == footer:
                    found_footer = True

                if not found_header or found_footer:
                    known_hosts += line

            known_hosts_file.close()

        log.debug("Finished reading known hosts file.")

        with open(config["known_hosts"], 'w') as known_hosts_file:
            known_hosts_file.write(known_hosts)
            known_hosts_file.close()

        log.debug("Finished writing known hosts file.")

    else:
        log.error("No known hosts file found, can't remove keyscanned node!")

async def remove_server(config, server_name):
    server = config["client"].servers.get_by_name(name=server_name)

    with dbconn(config) as cur:
        cur.execute("DELETE FROM hosts WHERE server_name = ?", (server_name,))

    await remove_keyscan(config, server_name)

    try:
        config["client"].servers.delete(server)
        log.debug("Found server and removed it.")
    except:
        log.debug("No server found to remove.")

async def check_unused_node(config, server_name):
    log.debug("Started checking if %s is unused, firstly sleeping for 50 minutes"%(server_name))

    delay_hour = 3000
    delay_ending_hour = 240
    await asyncio.sleep(delay_hour)

    with dbconn(config) as cur:
        host = cur.execute(
            "SELECT * FROM hosts WHERE server_name = ?", (server_name,)
        ).fetchone()
    host_id = host[0]

    removed = False
    while not removed:
        # how many times to check and sleep for 4 minutes after initial 50 minutes
        for counter in range(2):
            log.debug("Checking if node %s is unused"%(server_name))

            with dbconn(config) as cur:
                current_state = cur.execute(
                    "SELECT * FROM states WHERE host_id = ?", (host_id,)
                ).fetchone()

            current_state = current_state[3]

            if current_state != "active":
                log.info("Node %s marked as inactive and is being removed."%(server_name))
                await remove_server(config, server_name)
                removed = True
                break
            else:
                log.debug("Node %s marked as active, sleeping."%(server_name))
                await asyncio.sleep(delay_ending_hour)

        if not removed:
            await asyncio.sleep(delay_hour)


async def remove_known_hosts(config):
    log.info("Removing known hosts file.")

    if os.path.exists(config["known_hosts"]):
        log.debug("Found known hosts file. Removing it.")
        os.remove(config["known_hosts"])
    else:
        log.debug("No known hosts file found.")

async def remove_all_processes(config):
    log.info("Removing all processes from database.")
    
    with dbconn(config) as cur:
        processes = cur.execute("SELECT * FROM processes").fetchall()

    if len(processes) < 1:
        log.debug("No processes found.")
    else:
        log.debug("Removing all processes.")
        for process in processes:
            pid, host_id, process_id, cmd = process
            with dbconn(config) as cur:
                cur.execute("DELETE FROM processes WHERE id = ?", (pid,))

async def remove_all_nodes(config):
    log.info("Removing all nodes from database and HCloud.")
        
    with dbconn(config) as cur:
        hosts = cur.execute("SELECT * FROM hosts").fetchall()

    if len(hosts) < 1:
        log.debug("No nodes found.")
    else:
        log.debug("Removing all nodes.")
        for host in hosts:
            hid, hostname, weight, server_name = host
            log.debug("Removing node %s."%(server_name))
            await remove_server(config, server_name)


async def check_processes_and_rescale(config):
    while True:
        log.debug("Checking processes and rescaling.")

        with dbconn(config) as cur:
            hosts = cur.execute("SELECT * FROM hosts").fetchall()
            processes = cur.execute("SELECT * FROM processes").fetchall()

        if len(hosts) < 1:
            log.debug("No nodes found. Checking if there are any transcodes on fallback.")
            transcodes = 0
            for process in processes:
                pid, host_id, process_id, cmd = process
                if "transcode" in cmd:
                    transcodes += 1

            if transcodes > 0:
                log.info("Found transcodes on fallback!")
                asyncio.create_task(create_server(config))
        else:
            log.debug("Nodes found. Checking if there are any nodes with room.")
            nodes_with_room = 0
            for host in hosts:
                transcodes = 0
                hid, hostname, weight, server_name = host

                for process in processes:
                    pid, host_id, process_id, cmd = process
                    if host_id == hid and "transcode" in cmd:
                        transcodes += 1

                if transcodes < 2:
                    nodes_with_room += 1

            if nodes_with_room == 0:
                log.debug("No nodes with room found.")
                asyncio.create_task(create_server(config))
            else:
                log.debug("Nodes with room found.")

        log.debug("Sleeping for 5 minutes until next check.")
        await asyncio.sleep(300)


async def main():
    print("Starting HCloud!")
    await asyncio.sleep(30)

    # Setup
    config = setup()
    setup_logger(config)

    if not Path(config["db_path"]).is_file():
        fail("Failed to find database %s - did you forget to run 'rffmpeg init'?"%(config["db_path"]))

    # Removing old processes and nodes
    await remove_known_hosts(config)
    await remove_all_processes(config)
    await remove_all_nodes(config)

    # Running
    await check_processes_and_rescale(config)

    # Exit, it should never happen
    print("Stopping HCloud!")

# Startup
if __name__ == "__main__":
    asyncio.run(main())