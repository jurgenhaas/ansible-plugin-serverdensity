Ansible plugin for ServerDensity
================================

This is an [Ansible] plugin to manage your Ansible inventory over at [Server Density]. It uses the [ServerDensity API] and the [Ansible API].

##Features##

The following objects can be created and updated in your Server Density account from within Ansible:

* Hosts/Devices with groups
* Services with groups
* Alerts for
  * Devices
  * Device Groups
  * Services
  * Service Groups
* Notifications for alerts

There are plugin parameters to define how the plugin will behave:

**api_token**: An API token from Server Density to authenticate yourself
**force** (optional, defaults to False): If an object already exists whether it should be updated
**cache** (optional, defaults to None): Fully qualified filename for a cache of der Server Density data
**cleanup** (optional, defaults to False): Decides if undefined alerts in your Ansible inventory available at Server Density should be deleted

##Installation##

Download (or clone) the file serverdensity from the action_plugins directory and copy that into your custom action plugins directory which is defined in /etc/ansible/ansible.cfg. The default location for this is /usr/share/ansible_plugins/action_plugins

##Usage##

This plugin can be used in playbooks or with the ansible script directly.

###In Playbooks###

Simply include a task like this:

```
    - name: 'ServerDensity | Init SD plugin'
      local_action: serverdensity
        api_token={{sd_api_token}}
        cleanup=true
        cache='/tmp/my_sd_cache'
```

You may also be interested in a [Server Dernsity Role] that I've written which in addition installs and configures the Server Density agent on your hosts and works in conjunction with this plugin as well.

###From the ansible script###

Your whole inventory gets synchronized with Server Density simply by using this command:

```
ansible all -m serverdensity -a 'api_token=YOUR_SD_TOKEN' -vv
```

The final -vv parameter is inhancing the level of output on the console and with this plugin you'll get some quite useful information on what's going on in detail.

[Ansible]: http://www.ansible.com
[Server Density]: https://www.serverdensity.com
[ServerDensity API]: https://apidocs.serverdensity.com
[Ansible API]: http://docs.ansible.com/index.html
[Server Dernsity Role]: https://github.com/jurgenhaas/ansible-role-serverdensity
