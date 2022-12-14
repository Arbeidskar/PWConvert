from __future__ import annotations
import csv
import re
import shutil
import signal
import subprocess
from subprocess import TimeoutExpired
import os
import zipfile
import mimetypes
from pathlib import Path


def run_shell_command(command, cwd=None, timeout=60, shell=False) -> int:
    """
    Run the given command as a subprocess

    Args:
        command: The child process that should be executed
        cwd: Sets the current directory before the child is executed
        timeout: The number of seconds to wait before timing out the subprocess
        shell: If true, the command will be executed through the shell.
    Returns:
        exit code
    """
    os.environ["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        command,
        cwd=cwd,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ,
        universal_newlines=True,
    )

    try:
        _, errs = proc.communicate(timeout=timeout)
    except TimeoutExpired:
        proc.kill()
    except Exception as e:
        print(command)
        print(e)


    return proc.returncode


def make_filelist(source_dir: str, filelist_path: str) -> None:
    os.chdir(source_dir)
    subprocess.run('find -type f -not -path "./.*" > ' + filelist_path, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL, shell=True)


def run_file_command(source_dir: str, target_dir: str, tsv_path: str, zipped=False) -> None:
    filelist_path = os.path.join(target_dir, "filelist.txt")
    csv_path = os.path.join(target_dir, "siegfried.csv")
    os.chdir(source_dir)
    if not os.path.isfile(filelist_path):
        subprocess.run('find -type f -not -path "./.*" > ' + filelist_path, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL, shell=True)
    subprocess.run("echo 'filename, mime' > " + csv_path, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL, shell=True)
    subprocess.run('file -e compress -F , -N -P bytes=4096 --mime-type -f ' + filelist_path + ' >> ' + csv_path, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL, shell=True)
    csv_to_tsv(csv_path, tsv_path)
    remove_file(filelist_path)
    remove_file(csv_path)


def run_siegfried(source_dir: str, target_dir: str, tsv_path: str, zipped=False) -> None:
    """
    Generate tsv file with info about file types by running

    Args:
        source_dir: the directory containing the files to be checked
        target_dir: The target directory where the csv file will be saved
        tsv_path: The target path for tsv file
        zipped: nothing...
    """

    csv_path = os.path.join(target_dir, "siegfried.csv")
    if not os.path.isfile(csv_path):
        os.chdir(source_dir)
        subprocess.run("sf -multi 256 -csv * > " + csv_path, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL, shell=True)

    csv_to_tsv(csv_path, tsv_path)

    remove_file(csv_path)

def csv_to_tsv(csv_path, tsv_path) -> None:
    with open(csv_path, "r") as csvin, open(tsv_path, "w") as tsvout:
        csvin = csv.reader((x.replace('\0', '') for x in csvin), skipinitialspace=True)
        tsvout = csv.writer(tsvout, delimiter="\t")
        for row in csvin:
            tsvout.writerow(row)

def remove_file(src_path: str) -> None:
    if os.path.exists(src_path):
        os.remove(src_path)


def delete_file_or_dir(path: str) -> None:
    """Delete file or directory tree"""
    if os.path.isfile(path):
        os.remove(path)

    if os.path.isdir(path):
        shutil.rmtree(path)


def str_to_bool(s) -> bool:
    if s == "True":
        return True
    elif s == "False":
        return False
    else:
        raise ValueError


def extract_nested_zip(zipped_file: str, to_folder: str) -> None:
    """Extract nested zipped files to specified folder"""
    with zipfile.ZipFile(zipped_file, "r") as zfile:
        zfile.extractall(path=to_folder)

    for root, dirs, files in os.walk(to_folder):
        for filename in files:
            if re.search(r"\.zip$", filename):
                filespec = os.path.join(root, filename)
                extract_nested_zip(filespec, root)


def get_property_defaults(properties, overwrites) -> dict:
    if not overwrites:
        return properties

    return _merge_dicts(dict(properties), dict(overwrites))


def _merge_dicts(properties, overwrite_with):
    if isinstance(overwrite_with, dict):
        for k, v in overwrite_with.items():
            if k in properties:
                properties[k] = _merge_dicts(properties.get(k), v)
        return properties
    else:
        return overwrite_with
