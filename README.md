Setup ServerDensity by Ansible
==============================

This script is both using the API from ServerDensity and from Ansible.

It currently creates a node at ServerDensity for each host in you Ansible
inventory and by using the Ansible facts, those nodes get as much detail as
available and allowed by the Server Density API.

Future plans are to also create services and alerts from variables being
defined in the Ansible inventory.

##Usage##

Just clone the script ansible-serverdensity.py and run it, the possible
parameters are similar to those of Ansible in general and will be displayed
at the console.

With no parameters given, the default inventory on your system will be used.

There are several ways to make available the API Token from ServerDensity:

* Command line parameter: **-A**
* As a variable in your inventory: **sd_api_token**
* As an environment variable: **SD_API_TOKEN**
