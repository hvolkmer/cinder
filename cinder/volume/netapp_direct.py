# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2012 Telekom Innovation Laboratories
# Copyright (c) 2012 OpenStack LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
Volume driver for NetApp storage systems.

This driver works with ONTAP 7-mode storage systems with
installed iSCSI licenses. In contrast to the other NetApp driver
this requires no other software.

Configuration on NetApp needed:

  1. create igroup named "openstack"
     (or whatever you chose for 'netapp_direct_igroup_name')
     > igroup create -i -t linux openstack

  2. create NetApp volume that you want to use for LUNs
     > vol create openstack -d <disk-name1> <disk-name2> ... <disk-nameN>

The volume name on the NetApp system must match 'netapp_direct_volpool_name'.
Default is 'vol0'.
"""

import time
import string
import re

from cinder import exception
from cinder import flags
from cinder.openstack.common import log as logging
from cinder.openstack.common import cfg
from cinder.volume import driver
from cinder.volume import volume_types
from cinder.volume import san


LOG = logging.getLogger("cinder.volume.driver")

netapp_direct_opts = [
    cfg.StrOpt('netapp_direct_volpool_name',
               default='vol0',
               help='Storage system storage pool for volumes'),
    cfg.StrOpt('netapp_direct_login',
               default=None,
               help='User name for the netapp system'),
    cfg.StrOpt('netapp_direct_igroup_name',
               default='openstack',
               help='igroup name for the netapp system'),
    cfg.StrOpt('netapp_direct_password',
               default=None,
               help='Password for the netapp system'),
    cfg.StrOpt('netapp_direct_host',
               default=None,
               help='Host or IP for the netapp system'),
    cfg.StrOpt('netapp_direct_iscsi_portal_ip',
               default=None,
               help='The IP of the iscsi portal of the netapp system'),
    cfg.StrOpt('netapp_direct_iscsi_portal_port',
               default=3260,
               help='The IP of the iscsi portal of the netapp system')
]

FLAGS = flags.FLAGS
FLAGS.register_opts(netapp_direct_opts)


class NetAppDirectDriver(san.SanISCSIDriver):
    """NetApp iSCSI volume driver."""

    def __init__(self, *args, **kwargs):
        super(NetAppDirectDriver,  self).__init__(*args, **kwargs)
        self.igroup = FLAGS.netapp_direct_igroup_name
        self.portal_ip = FLAGS.netapp_direct_iscsi_portal_ip or FLAGS.san_ip
        self.portal_port = FLAGS.netapp_direct_iscsi_portal_port
        self.netapp_volume = FLAGS.netapp_direct_volpool_name


    def create_volume(self, volume):
        """
        Driver entry point for creating a new volume.

        """
        size = int(volume['size'])
        volume_name = volume['name']
        lun = self._get_lun_path_from_volume_name(volume_name)
        command = "lun create -s %sg -t linux %s" % (size, lun)
        # Sometimes the message
        # 'lun create: created a LUN of size <size>G' will be shown.
        # So we cannot check for empty "out". We just check "err" instead.
        self._ensure_ssh_cmd_succeeded(command, "Error while creating lun",
                                                lambda out, err: len(err) == 0)
        self._map_volume(lun)

    def delete_volume(self, volume):
        """Driver entry point for destroying existing volumes."""
        lun = self._get_lun_path_from_volume_name(volume['name'])
        command = "lun unmap %s %s" % (lun, self.igroup)
        self._ensure_ssh_cmd_succeeded(command, "Error while unmapping lun")
        command = "lun destroy %s" % lun
        self._ensure_ssh_cmd_succeeded(command, "Error while destroying lun")

    def ensure_export(self, context, volume):
        """
        Driver entry point to get the iSCSI details about an existing volume.
        """
        pass

    def create_export(self, context, volume):
        """
        Driver entry point to get the iSCSI details about a new volume.

        The LUN is not mapped at this point so we cannot get the full iSCSI
        path.
        """
        lun_id = self._get_lun_id_by_name(volume['name'])
        return {'provider_location': lun_id}

    def initialize_connection(self, volume, connector):
        """
        add attaching host to igroup so that LUN can be seen
        """
        initiator = connector['initiator']
        command = "igroup add -f %s %s" % (self.igroup, initiator)
        self._ensure_ssh_cmd_succeeded(command,
                                       "error initializing connection")
        properties = {}
        properties['target_discovered'] = False
        properties['target_portal'] = '%s:%s' % (self.portal_ip,
                                                 self.portal_port)
        properties['target_iqn'] = self._get_iscsi_nodename()
        properties['target_lun'] = self._get_lun_id_by_name(volume['name'])
        properties['volume_id'] = volume['id']
        return {
            'driver_volume_type': 'iscsi',
            'data': properties,
        }

    def remove_export(self, _ctx, volume):
        pass

    def terminate_connection(self, volume, connector):
        """
        When the connection is ended the LUN will be unmasked
        """
        initiator = connector['initiator']
        command = "igroup remove -f %s %s" % (self.igroup, initiator)
        self._ensure_ssh_cmd_succeeded(command, "error terminating connection")

    def create_snapshot(self, snapshot):
        """
        create a snapshot
        """
        volume_lun = self._get_lun_path_from_volume_name(
                                                      snapshot['volume_name'])
        snapshot_lun = self._get_lun_path_from_volume_name(snapshot['name'])
        self._clone_lun(volume_lun, snapshot_lun)

    def delete_snapshot(self, snapshot):
        self.delete_volume(snapshot)

    def create_volume_from_snapshot(self, volume, snapshot):
        volume_lun = self._get_lun_path_from_volume_name(volume['name'])
        snapshot_lun = self._get_lun_path_from_volume_name(snapshot['name'])
        target_size = volume['size']
        self._clone_lun(snapshot_lun, volume_lun)
        self._resize_volume(volume_lun, target_size)

    def check_for_export(self, context, volume_id):
        raise NotImplementedError()

    def _clone_lun(self, source_lun_path, destination_lun_path):
        command = "clone start %s %s" % (source_lun_path, destination_lun_path)
        self._ensure_ssh_cmd_succeeded(command, "error while cloing volume",
                lambda out, err: 'Clone operation started successfully' in out)
        # TODO: check if snapshot operation
        # clone status vol0 => check if current volume is in output
        self._map_volume(destination_lun_path)

    def _get_lun_path_from_volume_name(self, volume_name):
        return "/vol/%s/%s" % (self.netapp_volume, volume_name)

    def _map_volume(self, lun):
        command = "lun map %s %s" % (lun, self.igroup)
        self._ensure_ssh_cmd_succeeded(command, "Error while mapping lun")

    def _resize_volume(self, volume_lun, target_size):
        command = "lun resize %s %sg" % (volume_lun, target_size)
        self._ensure_ssh_cmd_succeeded(command, "Resize failed")

    def _get_lun_id_by_name(self, lun_path):
        volume_lun_mapping, err = self._run_ssh("lun show -m %s" %
                                self._get_lun_path_from_volume_name(lun_path))
        self._driver_assert(len(err) == 0, "LUN Mapping failed")
        match = re.search('%s.*([0-9]+).*iSCSI' % lun_path, volume_lun_mapping)
        return match.group(1).strip()

    def _get_iscsi_nodename(self):
        node_name, err = self._run_ssh("iscsi nodename")
        self._driver_assert(len(err) == 0, "getting node name failed")
        return re.search('iSCSI target nodename:(.*)', node_name).group(1).strip()

    def _ensure_ssh_cmd_succeeded(self, ssh_cmd, error_message,
                       condition=lambda out, err: len(out) == len(err) == 0):
        out, err = self._run_ssh(ssh_cmd)
        self._driver_assert(condition(out, err),
          _('%(error)s\nCommand: %(cmd)s\n' +
            'stdout: >%(out)s<\n stderr: >%(err)s<')
                % {'error': error_message,
                   'cmd': ssh_cmd,
                   'out': str(out),
                   'err': str(err)})

    def _driver_assert(self, assert_condition, exception_message):
        """
        Internal assertion mechanism for CLI output.
        Copied from storwize_svc driver
        """
        if not assert_condition:
            LOG.error(exception_message)
            # Change to VolumeBackendAPIException in next nova release
            raise exception.Error(exception_message)

