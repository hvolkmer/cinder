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

from cinder import exception
from cinder import test
from cinder.volume import netapp_direct
import cinder.flags

import mox

FLAGS = cinder.flags.FLAGS

#
# TODO: Add checks for SSH commands (with right parameters)
#

class TestNetAppDirectDriver(test.TestCase):

  def setUp(self):
      super(TestNetAppDirectDriver, self).setUp()
      FLAGS.san_password = "netapp"
      FLAGS.san_login = "root"
      FLAGS.san_ip = "192.168.133.3"
      self.connector = {'ip': '10.0.0.2',
                        'initiator': 'iqn.1993-08.org.debian:01:222',
                        'host': 'fakehost'}
      self.stubs.Set(netapp_direct.NetAppDirectDriver, "_run_ssh", self._fake_run_ssh)

      self.driver = netapp_direct.NetAppDirectDriver()

  def _fake_run_ssh(self, ssh_cmd):
      out = ""
      err = ""
      if ssh_cmd.startswith("clone start"):
          out = "Clone operation started successfully"
      return out, err

  def test_volume_creation(self):
      volume = {
        'size': 1,
        'name': "volume-000001",
        'id': 1
      }

      self.driver.create_volume(volume)

  def test_volume_deletion(self):
      volume = {
        'size': 1,
        'name': "volume-000001",
        'id': 1
      }

      self.driver.delete_volume(volume)

  def test_snapshot_creation(self):
      snapshot = {
        'volume_name': "volume-000001",
        'name': "snap-000001"
      }

      self.driver.create_snapshot(snapshot)

  def test_snapshot_deletion(self):
      snapshot = {
        'volume_name': "volume-000001",
        'name': "snap-000001"
      }

      self.driver.delete_snapshot(snapshot)

  def test_volume_creation_based_on_snapshot(self):
      snapshot = {
        'volume_name': "volume-000001",
        'name': "snap-000001"
      }

      snapped_volume = {
        'size': 2,
        'name': "volume-000002",
        'id': 2
      }

      self.driver.create_volume_from_snapshot(snapped_volume, snapshot)

