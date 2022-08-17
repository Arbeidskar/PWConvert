# Copyright(C) 2022 Morten Eek

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import pathlib
from os.path import relpath
from pathlib import Path
from typing import Dict
from argparse import ArgumentParser, Namespace

import petl as etl
from petl.io.db import DbView
from ruamel.yaml import YAML

# Load converters
from storage import ConvertStorage, StorageSqliteImpl
from util import run_siegfried, remove_file, File, Result
from util.util import get_property_defaults, str_to_bool

yaml = YAML()
with open("converters.yml", "r") as yamlfile:
    converters = yaml.load(yamlfile)
with open("application.yml", "r") as properties:
    properties = yaml.load(properties)

# Properties set in the local file will overwrite those in application.yml
with open("application.local.yml", "r") as local_properties:
    local_properties = yaml.load(local_properties)

pwconv_path = pathlib.Path(__file__).parent.resolve()


def remove_fields(table, *args):
    """Remove fields from petl table"""
    for field in args:
        if field in etl.fieldnames(table):
            table = etl.cutout(table, field)
    return table


def add_fields(table, *args):
    """Add fields to petl table"""
    for field in args:
        if field not in etl.fieldnames(table):
            table = etl.addfield(table, field, None)
    return table


def convert_folder_entrypoint(args: Namespace):
    with StorageSqliteImpl(args.db_path, args.db_name, args.resume) as file_storage:
        convert_folder(args.source, args.target, file_storage)


def convert_folder(source_dir: str,
                   target_dir: str,
                   file_storage: ConvertStorage,
                   zipped: bool = False):
    """Convert all files in folder"""
    tsv_source_path = target_dir + '.tsv'
    converted_now = False
    errors = False

    Path(target_dir).mkdir(parents=True, exist_ok=True)

    if not os.path.isfile(tsv_source_path):
        run_siegfried(source_dir, target_dir, tsv_source_path, zipped)

    row_count = write_sf_file_to_storage(
        tsv_source_path, source_dir, file_storage)
    table = file_storage.get_unconverted_rows(source_dir)

    file_count = sum([len(files) for r, d, files in os.walk(source_dir)])

    if row_count == 0:
        print('No files to convert. Exiting.')
        return 'Error', file_count
    if file_count != row_count:
        print(f'Row count: {str(row_count)}')
        print(f'File count: {str(file_count)}')
        print(
            f"Files listed in '{tsv_source_path}' doesn't match files on disk. Exiting.")
        return 'Error', file_count
    if not zipped:
        print('Converting files..')

    # print the files in this directory that have already been converted
    file_count = print_converted_files(file_count, file_storage, source_dir)

    # run conversion
    converted_now, errors, file_count = convert_files(
        converted_now, errors, file_count,
        source_dir, table, target_dir, file_storage, zipped
    )
    msg = get_conversion_result(converted_now, errors)
    return msg, file_count, errors


def convert_files(converted_now: bool,
                  errors: bool,
                  file_count: int,
                  source_dir: str,
                  table: DbView,
                  target_dir: str,
                  file_storage: ConvertStorage,
                  zipped: bool):
    table.row_count = 0
    for row in etl.dicts(table):
        # Remove Thumbs.db files
        if os.path.basename(row['source_file_path']) == 'Thumbs.db':
            remove_file(row['source_file_path'])
            file_count -= 1
            continue

        table.row_count += 1
        converted_now, errors = convert_file(converted_now, errors, file_count, file_storage, row, source_dir, table,
                                             target_dir, zipped)
    return converted_now, errors, file_count


def convert_file(
        converted_now: bool,
        errors: bool,
        file_count: int,
        file_storage: ConvertStorage,
        row: Dict[str, any],
        source_dir: str,
        table: DbView,
        target_dir: str,
        zipped: bool):
    row['mime_type'] = row['mime_type'].split(';')[0]
    if not row['mime_type']:
        # Siegfried sets mime type only to xml files with xml declaration
        if os.path.splitext(row['source_file_path'])[1].lower() == '.xml':
            row['mime_type'] = 'application/xml'
    if not zipped:
        print(
            f"({str(table.row_count)}/{str(file_count)}): .../{row['source_file_path']} ({row['mime_type']})'")
    
    source_file = File(row, converters, pwconv_path,
                       file_storage, convert_folder)
    normalized = source_file.convert(source_dir, target_dir)
    row['result'] = normalized['msg']
    if row['result'] in (Result.FAILED, Result.NOT_SUPPORTED):
        errors = True
        print(f"{row['mime_type']} {row['result']}")
    if row['result'] in (Result.SUCCESSFUL, Result.MANUAL):
        converted_now = True
    if normalized['norm_file_path']:
        row['norm_file_path'] = relpath(normalized['norm_file_path'], start=target_dir)

    file_storage.update_row(row['source_file_path'], list(row.values()))
    return converted_now, errors


def write_sf_file_to_storage(tsv_source_path: str, source_dir: str, file_storage: ConvertStorage):
    table = etl.fromtsv(tsv_source_path)
    table = etl.rename(table,
                       {
                           'filename': 'source_file_path',
                           'tika_batch_fs_relative_path': 'source_file_path',
                           'filesize': 'file_size',
                           'mime': 'mime_type',
                           'Content_Type': 'mime_type',
                           'Version': 'version'
                       },
                       strict=False)
    table = etl.select(table, lambda rec: rec.source_file_path != '')
    # Remove listing of files in zip
    table = etl.select(table, lambda rec: '#' not in rec.source_file_path)
    table = add_fields(table, 'version', 'norm_file_path', 'result', 'id')
    table = etl.addfield(table, 'source_directory', source_dir)
    # Remove Siegfried generated columns
    table = remove_fields(table, 'namespace', 'basis', 'warning')
    # TODO: Ikke fullgod sjekk på embedded dokument i linje over da # faktisk kan forekomme i filnavn

    # Treat csv (detected from extension only) as plain text:
    table = etl.convert(table, 'mime_type',
                        lambda v, _row: 'text/plain' if _row.id == 'x-fmt/18' else v,
                        pass_row=True)

    # Update for missing mime types where id is known:
    table = etl.convert(table, 'mime_type',
                        lambda v, _row: 'application/xml' if _row.id == 'fmt/979' else v,
                        pass_row=True)

    file_storage.append_rows(table)
    row_count = etl.nrows(table)
    remove_file(tsv_source_path)
    return row_count


def print_converted_files(file_count: int, file_storage: ConvertStorage, source_dir: str):
    converted_files = file_storage.get_converted_rows(source_dir)
    already_converted = etl.nrows(converted_files)

    before = file_count
    file_count -= already_converted
    if already_converted > 0:
        print(
            f'({already_converted}/{before}) files have already been converted  in {source_dir}')

    return file_count


def get_conversion_result(converted_now: bool, errors: bool):
    if converted_now:
        msg = 'All files converted successfully.'
        if errors:
            msg = "Not all files were converted. See the db table for details."
    else:
        msg = 'All files converted previously.'
    print("\n" + msg)
    return msg


def create_args_parser(parser: ArgumentParser):
    defaults = get_property_defaults(properties, local_properties)
    parser.add_argument('-s', '--source', help='Absolute path to the source directory.',
                        default=defaults['directories']['source'])
    parser.add_argument('-t', '--target', help='Absolute path to the target directory.',
                        default=defaults['directories']['target'])
    parser.add_argument('-dp', '--db-path', help='Absolute path to the database file',
                        default=defaults['database']['path'])
    parser.add_argument('-dn', '--db-name', help='Name of the the db file',
                        default=defaults['database']['name'])
    parser.add_argument('-r', '--resume',
                        help='Boolean value - True to resume a previous conversion, False to convert all files in the folder.',
                        default=defaults['database']['continue-conversion'], type=lambda x: str_to_bool(x), choices=(True, False))


parser = ArgumentParser('convert.py')
create_args_parser(parser)

if __name__ == "__main__":
    args = parser.parse_args()
    convert_folder_entrypoint(args)
