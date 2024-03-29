===========
Hypervisors
===========

.. toctree::
   :maxdepth: 1

   hypervisor-kvm
   hypervisor-qemu
   hypervisor-lxc
   hypervisor-vmware
   hypervisor-virtuozzo
   hypervisor-zvm
   hypervisor-ironic

OpenStack Compute supports many hypervisors, which might make it difficult for
you to choose one. Most installations use only one hypervisor.  However, you
can use :ref:`ComputeFilter` and :ref:`ImagePropertiesFilter` to schedule
different hypervisors within the same installation.  The following links help
you choose a hypervisor.  See :doc:`/user/support-matrix` for a detailed list
of features and support across the hypervisors.

The following hypervisors are supported:

* `KVM`_ - Kernel-based Virtual Machine. The virtual disk formats that it
  supports is inherited from QEMU since it uses a modified QEMU program to
  launch the virtual machine. The supported formats include raw images, the
  qcow2, and VMware formats.

* `LXC`_ - Linux Containers (through libvirt), used to run Linux-based virtual
  machines.

* `QEMU`_ - Quick EMUlator, generally only used for development purposes.

* `VMware vSphere`_ 5.1.0 and newer - Runs VMware-based Linux and Windows
  images through a connection with a vCenter server.

* `Virtuozzo`_ 7.0.0 and newer - OS Containers and Kernel-based Virtual
  Machines supported. The supported formats include ploop and qcow2 images.

* `zVM`_ - Server virtualization on z Systems and IBM LinuxONE, it can run Linux,
  z/OS and more.

* `Ironic`_ - OpenStack project which provisions bare metal (as opposed to virtual)
  machines.

Nova supports hypervisors via virt drivers. Nova has the following in tree
virt drivers:

* :oslo.config:option:`compute_driver` = ``libvirt.LibvirtDriver``

  This driver runs on Linux and supports multiple hypervisor backends, which
  can be configured via the :oslo.config:option:`libvirt.virt_type` config
  option.

* :oslo.config:option:`compute_driver` = ``ironic.IronicDriver``

* :oslo.config:option:`compute_driver` = ``vmwareapi.VMwareVCDriver``

* :oslo.config:option:`compute_driver` = ``zvm.ZVMDriver``

* :oslo.config:option:`compute_driver` = ``fake.FakeDriver``

  This driver does not spawn any virtual machines and therefore should only be
  used during testing.

.. _KVM: https://www.linux-kvm.org/page/Main_Page
.. _LXC: https://linuxcontainers.org
.. _QEMU: https://wiki.qemu.org/Manual
.. _VMware vSphere: https://www.vmware.com/support/vsphere-hypervisor.html
.. _Virtuozzo: https://www.virtuozzo.com/products/vz7.html
.. _zVM: https://www.ibm.com/it-infrastructure/z/zvm
.. _Ironic: https://docs.openstack.org/ironic/latest/
