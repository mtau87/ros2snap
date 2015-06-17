#!/usr/bin/env python

# Usage: python generate_snappy_metadata.py <-dbcms> <package>
# -d: Build from installed debians instead of source
# -m: Enable/disable generating snappy metadata
# -c: Enable/disable copying of recursive dependencies
# -s: Enable/disable building of snap
#
# If building from source (default), run at the root of a catkin workspace

import bloom.generators.common
import catkin_pkg.packages

import apt
import argparse
import os
import shutil
import stat
import sys

core_rosdeps = ['rosclean', 'rosmaster', 'rosout', 'rosmake']

def check_create_dir(dirname):
  if not os.path.exists(dirname):
    os.makedirs(dirname)

# Resolve and recursively copy dependencies
class SnappyBuilder:
  def __init__(self, package_key, packages, pkg_root):
    self.copied_packages = set()
    self.package_key = package_key
    self.package_key_final = package_key.replace("_", "-").lower()
    self.package = packages[package_key]
    if self.package is None:
      print "Requested package " + self.package_key + " not found, abort."
      sys.exit(-1)

    self.distro = os.getenv("ROS_DISTRO", "jade")

    self.snappy_root = "snappy_build/" + self.package_key_final + "/"
    self.snappy_meta_dir = self.snappy_root + "meta/"
    self.snappy_bin_dir = self.snappy_root + "bin/"
    self.pkg_root = pkg_root
    print "Package root: " + pkg_root
    setup = ". $mydir/opt/ros/%s/setup.bash\n" % self.distro
    if self.pkg_root == "install/":
      # Bash voodoo workaround to correct the install/setup.sh path
      # TODO a similar workaround may be needed to make opt/ros/indigo/setup.bash work
      local_setup = "$mydir/%s%s/setup.bash" % (self.pkg_root, self.package_key_final)
      #setup += "_CATKIN_SETUP_DIR=$mydir/%s%s\n" % (self.pkg_root, self.package_key_final)
      setup += "sed -i 's!^_CATKIN_SETUP_DIR=.*$!_CATKIN_SETUP_DIR='$mydir'/%s%s!' %s\n" % (self.pkg_root, self.package_key_final, local_setup)
      setup += ". %s\n" % local_setup

    self.environment_script = """#!/bin/bash
mydir=$(dirname $(dirname $(builtin cd "`dirname "${BASH_SOURCE[0]}"`" > /dev/null && pwd)))
%s
export ROS_MASTER_URI=http://localhost:11311
export LD_LIBRARY_PATH=$mydir/usr/lib/x86_64-linux-gnu:$mydir/usr/lib:$LD_LIBRARY_PATH
export PATH=$mydir/opt/ros/%s/bin:$mydir/usr/bin:$PATH
export PYTHONPATH=$mydir/opt/ros/%s/lib/python2.7/dist-packages:$mydir/usr/lib/python2.7/dist-packages:$PYTHONPATH
export PKG_CONFIG_PATH=$mydir/usr/lib/pkgconfig:$mydir/usr/lib/x86_64-linux-gnu/pkgconfig:$PKG_CONFIG_PATH
export CMAKE_PREFIX_PATH=$CMAKE_PREFIX_PATH:$mydir/opt/ros/%s/
""" % (self.distro, self.distro, self.distro, setup)

    self.cache = apt.Cache()

  def resolve_and_copy(self, key):
    # Resolve key to system package name
    # TODO: Cross-platform solution for args
    key_entry = bloom.generators.common.resolve_rosdep_key(key, "ubuntu", "trusty")
    if key_entry is None or (key_entry[0] is None) or (len(key_entry[0]) == 0):
      return

    apt_name = key_entry[0][0]
    self.copy_from_apt_cache(apt_name)

  def copy_files(self, run_dep_files):
    # Copy all the files
    for dep_path in run_dep_files:
      if os.path.isfile(dep_path):
        fullpath = self.snappy_root + dep_path
        check_create_dir(os.path.dirname(fullpath))
        shutil.copy2(dep_path, fullpath)

  def copy_from_apt_cache(self, apt_name):
    # Use apt python API to get all files
    run_pkg = self.cache[apt_name]

    # TODO for performance: exclusion rule for top-level packages (e.g. python)
    # that snappy instance already has
    if not apt_name.startswith("ros-" + self.distro + "-"):
      versions = run_pkg.versions
      # system packages
      if len(versions) > 0:
        version = versions[0]
        for dependency in version.dependencies:
          key = dependency[0].name
          if key in self.copied_packages or not self.cache.has_key(key):
            continue
          self.copied_packages.add(key)
          self.copy_from_apt_cache(key)

    # TODO: Catch more errors
    # TODO: install missing run deps with package manager
    # TODO: What about local installs?

    self.copy_files(run_pkg.installed_files)

  def copy_recursive_dependencies(self, package):
    run_dep_keys = [dep.name for dep in package.run_depends]

    for run_dep in run_dep_keys:
      if run_dep in self.copied_packages:
        continue
      self.resolve_and_copy(run_dep)
      self.copied_packages.add(run_dep)
      # Get the package.xml of the run dependencies if it's a ROS package
      ros_path = os.path.dirname(os.getenv("ROS_ROOT")) + "/" + run_dep
      packages = catkin_pkg.packages.find_packages(ros_path)
      for package in packages.values():
        self.copy_recursive_dependencies(package)

  def collect_binaries(self, path):
    install_dir = "install/" + path + "/" + self.package_key + "/"
    pkg_dir = self.snappy_root + install_dir
    if os.path.exists(pkg_dir):
      snappy_dir = self.snappy_bin_dir + path + "/"
      check_create_dir(snappy_dir)

      ret = ""
      for binary in os.listdir(pkg_dir):
        if os.access(pkg_dir + binary, os.X_OK) and os.path.isfile(pkg_dir + binary):
          binary_final = binary.replace("_", "-")
          script_path = snappy_dir + binary_final
          f = open(snappy_dir + binary_final, "w+")
          # TODO Parse python version for PYTHONPATH
          # is there an env variable to get the snap root...?
          script = self.environment_script + "$mydir/%s" % (install_dir + binary)

          f.write(script)
          f.close()
          st = os.stat(script_path)
          os.chmod(script_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
          ret += " - name: bin/" + path + "/" + binary_final + "\n"

      return ret

    return ""

  def create_dir_structure(self):
    # TODO reconsider deleting the entire snappy root, do this in chunks
    # based on command line args
    if os.path.exists(self.snappy_root):
      shutil.rmtree(self.snappy_root)

    check_create_dir("snappy_build")
    check_create_dir(self.snappy_meta_dir)
    check_create_dir(self.snappy_bin_dir)

  def copy_files_from_pkg_root(self):
    # If it's a valid root, first level will have bin, lib, include, share
    for path in os.listdir(self.pkg_root):
      # Check this folder for the package name
      if os.path.isdir(self.pkg_root + path) and (self.package_key in os.listdir(self.pkg_root + path)):
        # Copy the contents to snappy_root
        shutil.copytree(self.pkg_root + path + "/" + self.package_key,\
            self.snappy_root + "install/" + path + "/" + self.package_key_final)

  def parse_write_metadata(self):
    description = self.package.description

    # Inject description into readme.md
    f = open(self.snappy_meta_dir + "readme.md", "w+")
    f.write(self.package_key + "\n\n")
    f.write(description)
    f.close()

    # get first maintainer name and email
    maintainer_string = self.package.maintainers[0].name + " <" + self.package.maintainers[0].email + ">"

    # TODO icon, architecture

    version = self.package.version

    binaries_string = ""

    print "Checking lib, share, and launch for executables"
    binaries_string += self.collect_binaries("lib")
    binaries_string += self.collect_binaries("share")

    # Create scripts launching the launchfiles out of our package
    launchdir = self.pkg_root + "/share/" + self.package_key + "/launch/"
    if os.path.exists(launchdir):
      # Add roslaunch package
      self.resolve_and_copy("roslaunch")

      launchfiles = os.listdir(launchdir)
      check_create_dir(self.snappy_bin_dir + "launch")
      for launchfile in launchfiles:
        dst = self.snappy_bin_dir+ "launch/" + launchfile
        launch_string = self.environment_script + "roslaunch $mydir/" + launchdir + launchfile
        f = open(dst, "w+")
        f.write(launch_string)
        f.close()
        st = os.stat(dst)
        os.chmod(dst, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

      binaries_string += '\n'.join([" - name: bin/launch/" + launch for launch in launchfiles])

    data = "name: " + self.package_key_final + "\n" +\
           "version: " + version + "\n" +\
           "vendor: " + maintainer_string + "\n" +\
           "binaries:\n" + binaries_string

    f = open(self.snappy_meta_dir + "package.yaml", "w+")
    f.write(data)
    f.close()

  def copy_env_scripts(self):
    # with the correct dependencies copied, I think this function is unnecessary

    # Copy /opt/ros/<distro>/setup.bash and dependencies
    # better way of doing this?
    rospath = os.path.dirname(os.path.dirname(os.getenv("ROS_ROOT")))
    dstpath = self.snappy_root + rospath
    check_create_dir(dstpath)
    shutil.copy2(rospath + "/setup.bash", dstpath + "/setup.bash")
    shutil.copy2(rospath + "/setup.sh", dstpath + "/setup.sh")
    shutil.copy2(rospath + "/_setup_util.py", dstpath + "/_setup_util.py")

  def build(self, parse_metadata, copy_recursive_deps, build_snap):
    print "Building snap for package " + self.package_key

    if parse_metadata:
      print "Parsing metadata from package.xml and writing to meta/package.yaml"
      self.parse_write_metadata()
      self.copy_env_scripts()

    if copy_recursive_deps:
      print "Copying all recursive run dependencies into snap"
      self.copy_recursive_dependencies(self.package)
      for key in core_rosdeps:
        self.resolve_and_copy(key)

    self.cache.close()

    if build_snap:
      os.system("snappy build snappy_build/" + self.package_key_final)

def prepare_from_source(package_key):
  path = os.getcwd()

  packages = catkin_pkg.packages.find_packages(path + "/src/")

  if len(packages) == 0:
    print "No packages found in catkin workspace. Exiting."
    sys.exit(-1)

  os.system("catkin_make install")

  builder = SnappyBuilder(package_key, packages, "install/")

  builder.create_dir_structure()
  # Copy all files in install to snappy_build/<package>
  # If building from source, need to also get the setup files
  check_create_dir(builder.snappy_root + "install/" + builder.package_key_final)

  for path in os.listdir(builder.pkg_root):
    if os.path.isfile(builder.pkg_root + "/" + path):
      shutil.copy2(builder.pkg_root + "/" + path, \
          builder.snappy_root + "install/" + builder.package_key_final)

  builder.copy_files_from_pkg_root()
  return builder

def prepare_from_debs(package_key):
  # TODO: Install the package debian if it is not found

  ros_path = os.path.dirname(os.getenv("ROS_ROOT"))
  packages = catkin_pkg.packages.find_packages(ros_path)

  if len(packages) <= 0:
    print "Error: no catkin packages found in " + ros_path + ". Exiting."
    sys.exit(-1)

  builder = SnappyBuilder(package_key, packages, os.path.dirname(ros_path) + "/")

  builder.create_dir_structure()
  builder.copy_files_from_pkg_root()
  # get all the stuff we need that isn't under the package name
  key_entry = bloom.generators.common.resolve_rosdep_key(builder.package_key, "ubuntu", "trusty")
  if key_entry is None or (key_entry[0] is None) or (len(key_entry[0]) == 0):
    print "Apt couldn't find package with name " + package_key + ". Exiting."
    sys.exit(-1)

  apt_name = key_entry[0][0]
  pkg = builder.cache[apt_name]
  builder.copy_files(pkg.installed_files)
  return builder


def main():
  parser = argparse.ArgumentParser(description="Build a Snappy package (snap) from a ROS package.")
  parser.add_argument('package', help="Name of the ROS package to snap.")
  parser.add_argument('--debs', '-d', help="If true, build ROS package from debians. Else, build from catkin workspace rooted at current dir.", default=0)
  parser.add_argument('--metadata_generate', '-m', help="Enable/disable generating Snappy metadata for this package.", default=1)
  parser.add_argument('--copy_dependencies', '-c', help="Enable/disable copying all recursive dependencies for this package.", default=1)
  parser.add_argument('--snap_build', '-s', help="Enable/disable calling 'snappy_build' for this package.", default=1)

  if len(sys.argv) < 2:
    parser.print_usage()
    sys.exit(-1)

  parsed_args = parser.parse_args(sys.argv[1:])
  build_from_debs = int(parsed_args.debs)
  parse_metadata = int(parsed_args.metadata_generate)
  copy_recursive_deps = int(parsed_args.copy_dependencies)
  build_snap = int(parsed_args.snap_build)
  package = parsed_args.package
  if build_from_debs:
    builder = prepare_from_debs(package)
  else:
    builder = prepare_from_source(package)

  builder.build(parse_metadata, copy_recursive_deps, build_snap)


if __name__ == "__main__":
  main()
