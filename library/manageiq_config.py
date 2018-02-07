#!/usr/bin/python
# -*- coding: utf-8 -*-
# (c) 2018, Ian Tewksbury <itewk@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: manageiq_config

short_description: Module for managing ManageIQ configuration.
version_added: '2.5'
author: Ian Tewksbury (@itewk)
description:
    - "Module for managing ManageIQ or CloudForms Managment Engine (CFME) configuration via the local rails runner on the Appliance."

options:
    name:
        description:
            - Name of the ManageIQ config element to modify.
        required: True
    value:
        description: Dictionary to set as the value for the ManageIQ config option. Any values not given will be left at current value.
        required: False
        default: {}
    vmdb_path:
        description: Path to the VMDB directory.
        required: False
        default: /var/www/miq/vmdb
    confirm_update_max_retries:
        description: Number of times to attempt to confirm configuration has been updated.
        required: False
        default: 10
    confirm_update_sleep_interval:
        description: Number of seconds between retries for confirming configuration has been updated.
        required: False
        default: 1
'''

EXAMPLES = '''
# set the smtp settings
- manageiq_config:
    name: smtp
    value:
      from: cfme@example.com
      host: postfix.example.com
      port: 25
      domain: example.com

# set generic worker memory threshold
- manageiq_config:
    name: workers
    value:
      worker_base:
        queue_worker_base:
          generic_worker:
            memory_threshold: '900.megabytes'
'''

RETURN = '''
name:
    description: Name of the ManageIQ config option that is being modified.
    type: str
    returned: always
original_value:
    description: The origional value of the ManageIQ config option being modified before modification.
    type: dict
    returned: always
value:
    description: The value of the ManageIQ config option being modified after modification.
    type: dict
    returned: always
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.basic import json
import time
import collections
import copy


# @source https://gist.github.com/angstwad/bf22d1822c38a92ec0a9
def dict_merge(dct, merge_dct):
    """ Recursive dict merge. Inspired by :meth:``dict.update()``, instead of
    updating only top-level keys, dict_merge recurses down into dicts nested
    to an arbitrary depth, updating keys. The ``merge_dct`` is merged into
    ``dct``.
    :param dct: dict onto which the merge is executed
    :param merge_dct: dct merged into dct
    :return: None
    """
    for k, v in merge_dct.items():
        if (k in dct and isinstance(dct[k], dict)
                and isinstance(merge_dct[k], collections.Mapping)):
            dict_merge(dct[k], merge_dct[k])
        else:
            dct[k] = merge_dct[k]


def get_manageiq_config_value(module, name):
    """ Gets the current value for the given config option.
    :param module: AnsibleModule making the call
    :param name: ManageIQ config option name
    :return: dict of value of the ManageIQ config option
    """
    returncode, out, err = module.run_command([
        "rails",
        "r",
        "puts MiqServer.my_server.get_config(:%s).config.to_json" % (name)
    ], cwd=module.params['vmdb_path'])
    if returncode != 0:
        raise Exception("Error getting existing value for ':%s' config: %s" % (name, err))

    return json.loads(out)


def wait_for_manageiq_config_value_to_be_expected(module, expected_value):
    """ Waits until the given expected_value equals the current from ManageIQ.
    :param module: AnsibleModule making the call
    :param expected_value: The expected value from ManageIQ config
    :return: bool, true if expected value matches current value after retires, false otherwise.
    """
    # setting the configuration is an async operation which must be waited for for completion
    # NOTE: if anyone has a better idea how to do this please share
    retries = 0
    current_value = ''
    while expected_value != current_value and retries < module.params['confirm_update_max_retries']:
        try:
            current_value = get_manageiq_config_value(module, module.params['name'])
        except Exception as err:
            module.fail_json(msg=str(err))

        retries += 1
        time.sleep(module.params['confirm_update_sleep_interval'])

    return expected_value == current_value


def main():
    # define the module
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type='str', required=True),
            value=dict(type='dict', required=False, default={}),
            vmdb_path=dict(type='str', required=False, default='/var/www/miq/vmdb'),
            confirm_update_max_retries=dict(type='int', required=False, default=10),
            confirm_update_sleep_interval=dict(type='int', required=False, default=1)
        ),
        supports_check_mode=True
    )

    # seed the result dict in the object
    result = dict(
        changed=False,
        name='',
        original_value={},
        value={},
        diff={}
    )

    # get the origional value for the given config option
    try:
        original_value = get_manageiq_config_value(module, module.params['name'])
    except Exception as err:
        module.fail_json(msg=str(err), **result)

    # update the result
    result['name'] = module.params['name']
    result['original_value'] = original_value

    # create updated value dictionary
    update_value = copy.deepcopy(original_value)
    dict_merge(update_value, module.params['value'])

    # enable diff mode
    result['diff'] = {
        'before': {
            module.params['name']: original_value
        },
        'after': {
            module.params['name']: update_value
        }
    }

    # if check_mode then stop here
    if module.check_mode:
        result['changed'] = original_value != update_value
        module.exit_json(**result)

    # update config if difference
    # else no-op
    if original_value != update_value:
        (update_value_rc, update_value_out, update_value_err) = module.run_command([
            "rails",
            "r",
            "MiqServer.my_server.set_config(:%s => JSON.parse('%s'))" % (module.params['name'], json.dumps(update_value))
        ], cwd=module.params['vmdb_path'])
        result['changed'] = True
        if update_value_rc != 0:
            module.fail_json(msg="Error updating value for ':%s' config: %s" % (module.params['name'], update_value_err), **result)
    else:
        module.exit_json(**result)

    # wait for update value to equal current value
    current_matches_expected_value = wait_for_manageiq_config_value_to_be_expected(module, update_value)

    # if current ManageIQ config value equals expected value then exit with success
    # else exit with error
    if current_matches_expected_value:
        module.exit_json(**result)
    else:
        module.fail_json(
            msg="Timed out waiting for set config to take affect. { 'retries': %s, 'sleep_interval': %s }" % (
                module.params['confirm_update_max_retries'], module.params['confirm_update_sleep_interval']
            ),
            **result
        )


if __name__ == '__main__':
    main()
