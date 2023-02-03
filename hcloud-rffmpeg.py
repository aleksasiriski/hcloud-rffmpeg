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

    HCLOUD_API_TOKEN = os.getenv("HCLOUD_API_TOKEN")
    if HCLOUD_API_TOKEN == None:
        fail("HCLOUD_API_TOKEN env isn't set")

    JELLYFIN_LAN_ONLY_IP = os.getenv("JELLYFIN_LAN_ONLY_IP")
    if JELLYFIN_LAN_ONLY_IP == None:
        fail("JELLYFIN_LAN_ONLY_IP env isn't set")
    
    MEDIA_USERNAME = os.getenv("MEDIA_USERNAME", "")
    MEDIA_PASSWORD = os.getenv("MEDIA_PASSWORD", "")
    config["cloud_config"] = os.getenv("CLOUD_CONFIG", "#cloud-config\nruncmd:\n- systemctl disable --now ssh.service\n- echo 'JELLYFIN_LAN_ONLY_IP=%s' | tee -a /root/.env\n- echo 'MEDIA_USERNAME=%s' | tee -a /root/.env\n- echo 'MEDIA_PASSWORD=%s' | tee -a /root/.env\n- wget https://raw.githubusercontent.com/aleksasiriski/rffmpeg-worker/main/docker-compose.example.yml -O /root/docker-compose.yml\n- cd /root && docker compose pull && docker compose up -d\n"%(JELLYFIN_LAN_ONLY_IP, MEDIA_USERNAME, MEDIA_PASSWORD))

    config["state_dir"] = os.getenv("STATE_DIR", "/config")
    config["log_file"] = os.getenv("LOG_FILE", config["state_dir"] + "/log/hcloud-rffmpeg.log")
    config["db_path"] = os.getenv("DB_PATH", config["state_dir"] + "/rffmpeg/rffmpeg.db")
    config["ssh_key"] = os.getenv("SSH_KEY", config["state_dir"] + "/rffmpeg/.ssh/id_ed25519.pub")

    config["client"] = Client(token=HCLOUD_API_TOKEN)
    config["server_type"] = os.getenv("SERVER_TYPE", "cx21")
    config["image"] = Image(name=os.getenv("IMAGE_TYPE", "docker-ce"))
    config["networks"] = config["client"].networks.get_all(name=os.getenv("NETWORK_NAME", "rffmpeg-workers"))
    config["firewalls"] = config["client"].firewalls.get_all(name=os.getenv("FIREWALL_NAME", "rffmpeg-workers"))
    config["placement_group"] = config["client"].placement_groups.get_by_name(name=os.getenv("PLACEMENT_GROUP_NAME", "rffmpeg-workers"))
    config["location"] = Location(name=os.getenv("LOCATION_NAME", "nbg1"))
    config["jobs_per_worker"] = int(os.getenv("JOBS_PER_WORKER", "2"))
    config["recently_made_worker_bool"] = False

    SSH_KEY_NAME = os.getenv("SSH_KEY_NAME", "root@jellyfin")
    ssh_key = config["client"].ssh_keys.get_by_name(name=SSH_KEY_NAME)
    try:
        config["client"].ssh_keys.delete(ssh_key)
        log.debug("Found key and removed it.")
    except:
        log.debug("No key found to remove.")
    public_key = ""
    with open(config["ssh_key"], 'r') as ssh_key_file:
        public_key = ssh_key_file.readline()
        ssh_key_file.close()
    config["client"].ssh_keys.create(
        name=SSH_KEY_NAME,
        public_key=public_key
    )
    config["ssh_keys"] = config["client"].ssh_keys.get_all(name=SSH_KEY_NAME)

    return config

@contextmanager
def dbconn(config):
    conn = sqlite_connect(config["db_path"])
    conn.execute("PRAGMA foreign_keys = 1")
    cur = conn.cursor()
    yield cur
    conn.commit()
    conn.close()


async def recently_made_worker_timer(config, delay):
    await asyncio.sleep(delay)
    config["recently_made_worker_bool"] = False
    log.debug("Timer finished, able to make workers again!")

async def recently_made_worker(config):
    if config["recently_made_worker_bool"]:
        return True
    else:
        config["recently_made_worker_bool"] = True
        asyncio.create_task(recently_made_worker_timer(config, 180))
        return False

async def create_server(config):
    log.info("Creating a server!")

    if not await recently_made_worker(config):
        log.debug("No recently made servers!")

        response = config["client"].servers.create(
            server_type=config["server_type"],
            image=config["image"],
            ssh_keys=config["ssh_keys"],
            networks=config["networks"],
            firewalls=config["firewalls"],
            placement_group=config["placement_group"],
            location=config["location"],
            user_data=config["cloud_config"]
        )

        await asyncio.sleep(120)
        if response.action.status == "error":
            log.error("Error occured while creating the server in HCloud!")
        else:
            log.debug("Successfully created a server in HCloud!")

            server_name = response.model.name
            server_ip = config["client"].servers.get_by_name(name=server_name).private_net[0].ip

            with dbconn(config) as cur:
                cur.execute(
                    "INSERT INTO hosts (hostname, weight, servername) VALUES (?, ?, ?)",
                    (server_ip, 1, server_name),
                )

            log.info("Added %s with IP %s to database!"%(server_name,server_ip))
            asyncio.create_task(check_unused_worker(config, server_name))

    else:
        log.debug("Recently made a server!")

async def remove_server(config, server_name):
    server = config["client"].servers.get_by_name(name=server_name)

    with dbconn(config) as cur:
        cur.execute("DELETE FROM hosts WHERE server_name = ?", (server_name,))

    try:
        config["client"].servers.delete(server)
        log.debug("Found server and removed it.")
    except:
        log.debug("No server found to remove.")

async def check_unused_worker(config, server_name):
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
            log.debug("Checking if worker %s is unused"%(server_name))

            with dbconn(config) as cur:
                current_state = cur.execute(
                    "SELECT * FROM states WHERE host_id = ?", (host_id,)
                ).fetchone()

            if not current_state:
                current_state = "idle"
            else:
                current_state = current_state[3]

            if current_state != "active":
                log.info("Worker %s marked as inactive and is being removed."%(server_name))
                await remove_server(config, server_name)
                removed = True
                break
            else:
                log.debug("Worker %s marked as active, sleeping."%(server_name))
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

async def remove_all_workers(config):
    log.info("Removing all workers from database and HCloud.")
        
    with dbconn(config) as cur:
        hosts = cur.execute("SELECT * FROM hosts").fetchall()

    if len(hosts) < 1:
        log.debug("No workers found.")
    else:
        log.debug("Removing all workers.")
        for host in hosts:
            hid, hostname, weight, server_name = host
            log.debug("Removing worker %s."%(server_name))
            await remove_server(config, server_name)


async def check_processes_and_rescale(config):
    while True:
        log.debug("Checking processes and rescaling.")

        with dbconn(config) as cur:
            hosts = cur.execute("SELECT * FROM hosts").fetchall()
            processes = cur.execute("SELECT * FROM processes").fetchall()

        if len(hosts) < 1:
            log.debug("No workers found. Checking if there are any transcodes on fallback.")
            transcodes = 0
            for process in processes:
                pid, host_id, process_id, cmd = process
                if "transcode" in cmd:
                    transcodes += 1

            if transcodes > 0:
                log.info("Found transcodes on fallback!")
                asyncio.create_task(create_server(config))
        else:
            log.debug("Workers found. Checking if there are any workers with room.")
            workers_with_room = 0
            for host in hosts:
                transcodes = 0
                hid, hostname, weight, server_name = host

                for process in processes:
                    pid, host_id, process_id, cmd = process
                    if host_id == hid and "transcode" in cmd:
                        transcodes += 1

                if transcodes < config["jobs_per_worker"]:
                    workers_with_room += 1

            if workers_with_room == 0:
                log.debug("No workers with room found.")
                asyncio.create_task(create_server(config))
            else:
                log.debug("Workers with room found.")

        log.debug("Sleeping for 5 minutes until next check.")
        await asyncio.sleep(300)


async def main():
    print("Starting HCloud!")

    config = setup()
    setup_logger(config)

    if not Path(config["db_path"]).is_file():
        fail("Failed to find database %s - did you forget to run 'rffmpeg init'?"%(config["db_path"]))

    await remove_all_processes(config)
    await remove_all_workers(config)

    await check_processes_and_rescale(config)

    # Exit, it should never happen
    print("Stopping HCloud!")

# Startup
if __name__ == "__main__":
    asyncio.run(main())
