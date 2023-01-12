import os
import sys
import logging
import asyncio

from contextlib import contextmanager
from pathlib import Path
from sqlite3 import connect as sqlite_connect

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

    ID_RSA_PUB = os.getenv("ID_RSA_PUB")
    if ID_RSA_PUB == None:
        ID_RSA_PUB = STATE_DIR + "/.ssh/id_rsa.pub"
    config["id_rsa_pub"] = ID_RSA_PUB


    HCLOUD_API_TOKEN = os.getenv("HCLOUD_API_TOKEN")
    if HCLOUD_API_TOKEN == None:
        fail("HCLOUD_API_TOKEN env isn't set")

    JELLYFIN_LAN_ONLY_IP = os.getenv("JELLYFIN_LAN_ONLY_IP")
    if JELLYFIN_LAN_ONLY_IP == None:
        fail("JELLYFIN_LAN_ONLY_IP env isn't set")

    MEDIA_USERNAME = os.getenv("MEDIA_USERNAME")
    if MEDIA_USERNAME == None:
        fail("MEDIA_USERNAME env isn't set")

    MEDIA_PASSWORD = os.getenv("MEDIA_PASSWORD")
    if MEDIA_PASSWORD == None:
        fail("MEDIA_PASSWORD env isn't set")


    config["client"] = Client(token=HCLOUD_API_TOKEN)

    SERVER_TYPE = os.getenv("SERVER_TYPE")
    if SERVER_TYPE == None:
        SERVER_TYPE = "cx21"
    config["server_type"] = ServerType(name=SERVER_TYPE)

    IMAGE_TYPE = os.getenv("IMAGE_TYPE")
    if IMAGE_TYPE == None:
        IMAGE_TYPE = "docker-ce"
    config["image"] = Image(name=IMAGE_TYPE)


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


    NETWORK_NAME = os.getenv("NETWORK_NAME")
    if NETWORK_NAME == None:
        NETWORK_NAME = "rffmpeg"
    config["networks"] = config["client"].networks.get_all(name=NETWORK_NAME)

    FIREWALL_NAME = os.getenv("FIREWALL_NAME")
    if FIREWALL_NAME == None:
        FIREWALL_NAME = "rffmpeg"
    config["firewalls"] = config["client"].firewalls.get_all(name=FIREWALL_NAME)

    PLACEMENT_GROUP_NAME = os.getenv("PLACEMENT_GROUP_NAME")
    if PLACEMENT_GROUP_NAME == None:
        PLACEMENT_GROUP_NAME = "rffmpeg"
    config["placement_group"] = config["client"].placement_groups.get_by_name(name=PLACEMENT_GROUP_NAME)

    LOCATION_NAME = os.getenv("LOCATION_NAME")
    if LOCATION_NAME == None:
        LOCATION_NAME = "nbg1"
    config["location"] = Location(name=LOCATION_NAME)


    CLOUD_CONFIG = os.getenv("CLOUD_CONFIG")
    if CLOUD_CONFIG == None:
        CLOUD_CONFIG = "#cloud-config\nruncmd:\n- systemctl disable --now ssh.service\n- echo 'JELLYFIN_LAN_ONLY_IP=%s' | tee -a /root/.env\n- echo 'MEDIA_USERNAME=%s' | tee -a /root/.env\n- echo 'MEDIA_PASSWORD=%s' | tee -a /root/.env\n- fallocate -l 4G /swapfile\n- chmod 600 /swapfile\n- mkswap /swapfile\n- swapon /swapfile\n- echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab\n- wget https://raw.githubusercontent.com/aleksasiriski/rffmpeg-worker/main/docker-compose.example.yml -O /root/docker-compose.yml\n- cd /root && docker compose pull && docker compose up -d\n"%(JELLYFIN_LAN_ONLY_IP, MEDIA_USERNAME, MEDIA_PASSWORD)
    config["cloud_config"] = CLOUD_CONFIG


    JOBS_PER_NODE = os.getenv("JOBS_PER_NODE")
    if JOBS_PER_NODE == None:
        JOBS_PER_NODE = 2
    config["jobs_per_node"] = int(JOBS_PER_NODE)

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
            hid, hostname, weight, servername = host
            node_number = servername.split(name, 1)[1]
            if next_number <= node_number:
                next_number = node_number + 1
    
    new_name = name + str(next_number)
    log.debug("New name is " + new_name)

    return new_name

async def create_server(config):
    log.info("Creating a server!")

    if not await recently_made_node(config):
        log.debug("No recently made servers!")

        servername = await next_node_name(config)
        response = config["client"].servers.create(
            name=servername,
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
            await asyncio.sleep(120)
            log.debug("Successfully created a server in HCloud!")

            server_ip = config["client"].servers.get_by_name(name=servername).private_net[0].ip

            with dbconn(config) as cur:
                cur.execute(
                    "INSERT INTO hosts (hostname, weight, servername) VALUES (?, ?, ?)",
                    (server_ip, 1, servername),
                )

            log.info("Added %s with IP %s to database!"%(servername,server_ip))
            asyncio.create_task(check_unused_node(config, servername))

    else:
        log.debug("Recently made a server!")

async def remove_server(config, servername):
    server = config["client"].servers.get_by_name(name=servername)

    with dbconn(config) as cur:
        cur.execute("DELETE FROM hosts WHERE servername = ?", (servername,))

    try:
        config["client"].servers.delete(server)
        log.debug("Found server and removed it.")
    except:
        log.debug("No server found to remove.")

async def check_unused_node(config, servername):
    log.debug("Started checking if %s is unused, firstly sleeping for 50 minutes"%(servername))

    delay_hour = 3000
    delay_ending_hour = 240
    await asyncio.sleep(delay_hour)

    with dbconn(config) as cur:
        host = cur.execute(
            "SELECT * FROM hosts WHERE servername = ?", (servername,)
        ).fetchone()
    host_id = host[0]

    removed = False
    while not removed:
        # how many times to check and sleep for 4 minutes after initial 50 minutes
        for counter in range(2):
            log.debug("Checking if node %s is unused"%(servername))

            with dbconn(config) as cur:
                current_state = cur.execute(
                    "SELECT * FROM states WHERE host_id = ?", (host_id,)
                ).fetchone()

            if not current_state:
                current_state = "idle"
            else:
                current_state = current_state[3]

            if current_state != "active":
                log.info("Node %s marked as inactive and is being removed."%(servername))
                await remove_server(config, servername)
                removed = True
                break
            else:
                log.debug("Node %s marked as active, sleeping."%(servername))
                await asyncio.sleep(delay_ending_hour)

        if not removed:
            await asyncio.sleep(delay_hour)


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
            hid, hostname, weight, servername = host
            log.debug("Removing node %s."%(servername))
            await remove_server(config, servername)


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
                hid, hostname, weight, servername = host

                for process in processes:
                    pid, host_id, process_id, cmd = process
                    if host_id == hid and "transcode" in cmd:
                        transcodes += 1

                if transcodes < config["jobs_per_node"]:
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

    config = setup()
    setup_logger(config)

    if not Path(config["db_path"]).is_file():
        fail("Failed to find database %s - did you forget to run 'rffmpeg init'?"%(config["db_path"]))

    await remove_all_processes(config)
    await remove_all_nodes(config)

    await check_processes_and_rescale(config)

    # Exit, it should never happen
    print("Stopping HCloud!")

# Startup
if __name__ == "__main__":
    asyncio.run(main())
