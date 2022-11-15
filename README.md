# HCLOUD-RFFMPEG
## NOTICE
**This script currently works only with my [fork](https://github.com/aleksasiriski/jellyfin-rffmpeg-server) of [rffmpeg](https://github.com/joshuaboniface/rffmpeg) since I added a single name field to the DB config for hosts.**

## Setup
1) Set the required environment variables
2) Share the volume of rffmpeg config directory with jellyfin
3) Make sure the `cloud-init` string works manually first
4) All of your nodes need jellyfin config, cache and transcode directories available and mounted at the same path as the jellyfin host. Best solution is to use `NFSv4` with `sync` option.
5) Also, I'm assuming you're using Hetzner Storage Box so the defaults will work for you, but if you aren't using a network drive for media you will need to share that directory via NFS as well.

If you need a reference docker compose file use [this one](https://github.com/aleksasiriski/hcloud-rffmpeg/blob/latest/docker-compose.example.yml).

## Recommended images

I made and tested these images to use with this script:

1) [ghcr.io/aleksasiriski/jellyfin-rffmpeg-server](https://github.com/aleksasiriski/jellyfin-rffmpeg-server)
2) [ghcr.io/aleksasiriski/jellyfin-intro-skipper-rffmpeg-server](https://github.com/aleksasiriski/jellyfin-intro-skipper-rffmpeg-server)
3) [ghcr.io/aleksasiriski/jellyfin-rffmpeg-node](https://github.com/aleksasiriski/jellyfin-rffmpeg-node)

## Environment variables

| Name			| Default value		| Description		|
| :----------: | :--------------: | :--------------- | 
| STATE_DIR | /config/rffmpeg | Path to rffmpeg config |
| LOG_FILE | STATE_DIR + /hcloud-rffmpeg.log | Path to log file |
| DB_PATH | STATE_DIR + /rffmpeg.db | Path to SQLite database file |
| ID_RSA_PUB | STATE_DIR + /.ssh/id_rsa.pub | Path to rffmpeg public ssh key |
| NFS_LAN_IP | Must be explicitly set! | The IP address of the nfs share that nodes use to access jellyfin config, cache and transcode directories |
| HCLOUD_API_TOKEN | Must be explicitly set! | Hetzner Cloud API token |
| MEDIA_USERNAME | Must be explicitly set! | Username for the media share |
| MEDIA_PASSWORD | Must be explicitly set! | Password for the media share |
| SERVER_TYPE | cx21 | The type of server from Hetzner that should be used for nodes |
| IMAGE_TYPE | docker-ce | The OS image used on nodes, `docker-ce` is Ubuntu with Docker preinstalled |
| SSH_KEY_NAME | root@jellyfin | The name of the ssh key that will be saved on Hetzner and used for connecting to nodes |
| NETWORK_NAME | rffmpeg | The name of the network created for local communication between the nodes and the Jellyfin host
| FIREWALL_NAME | rffmpeg | The name of the firewall created for nodes, recommended to block access to ssh over the internet
| PLACEMENT_GROUP_NAME | rffmpeg | The name of the placement group created to spread the nodes over the datacenter |
| LOCATION_NAME | nbg1 | The name of the location in which the nodes should be created |
| CLOUD_CONFIG | [string](https://github.com/aleksasiriski/hcloud-rffmpeg/blob/latest/hcloud-rffmpeg.py#L138) | The string that setups the nodes after creation, the default uses my docker compose and inserts needed env variables |
| JOBS_PER_NODE | 2 | Number of jobs allowed per node, the default of 2 tells the script to only create a new node if there are 2 or more jobs on the previous one. |
