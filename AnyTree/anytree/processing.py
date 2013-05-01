import csv
import logging
from os.path import basename, join

logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(basename(__file__))


class CSVProcessor(object):
    """ Take a csv file, process it with the mapping
    and output a new csv file
    """
    def __init__(self, mapping):
        self.mapping = mapping
        self.target_columns = {}
        self.writers = {}

    def get_target_columns(self, filepaths):
        """ Compute target columns with source columns + mapping
        """
        target_columns = {}
        for filepath in filepaths:
            source_table = basename(filepath).rsplit('.', 1)[0]
            with open(filepath) as f:
                source_columns = csv.reader(f).next()
            for source_column in source_columns:
                mapping = self.mapping.get_targets('%s.%s' % (source_table, source_column))
                # no mapping found, we warn the user
                if mapping is None:
                    origin = source_table + '.' + source_column
                    LOG.warn('No mapping definition found for column %s', origin)
                    continue
                else:
                    for target in mapping:
                        t, c = target.split('.')
                        target_columns.setdefault(t, set()).add(c)
        return target_columns

    def process(self, source_dir, source_filenames, target_dir):
        """ The main processing method
        """
        # compute the target columns
        filepaths = [join(source_dir, source_filename) for source_filename in source_filenames]
        self.target_columns = self.get_target_columns(filepaths)
        # open target files for writing
        self.target_files = {
            table: open(join(target_dir, table + '.out.csv'), 'ab')
            for table in self.target_columns
        }
        # create csv writers
        self.writers = {t: csv.DictWriter(f, self.target_columns[t], delimiter=',')
                        for t, f in self.target_files.items()}
        # write csv headers once
        for writer in self.writers.values():
            writer.writeheader()
        # process csv files
        for source_filename in source_filenames:
            source_filepath = join(source_dir, source_filename)
            self.process_one(source_filepath)
        for target_file in self.target_files.values():
            target_file.close()

    def process_one(self, source_filepath):
        """ Process one csv file
        """
        source_table = basename(source_filepath).rsplit('.', 1)[0]
        with open(source_filepath, 'rb') as source_csv:
            reader = csv.DictReader(source_csv, delimiter=',')
            # process each csv line
            for source_row in reader:
                target_rows = {}
                # process each column
                for source_column in source_row:
                    mapping = self.mapping.get_targets(source_table + '.' + source_column)
                    if mapping is None:
                        continue
                    # we found a mapping, use it
                    for target_column, function in mapping.items():

                        target_table, target_column = target_column.split('.')
                        target_rows.setdefault(target_table, {})
                        if function is None:
                            # mapping is empty: use identity
                            target_rows[target_table][target_column] = source_row[source_column]
                        else:
                            # mapping is a function
                            result = function(source_row, target_rows)
                            target_rows[target_table][target_column] = result
                for table, target_row in target_rows.items():
                    if any(target_row.values()):
                        self.writers[table].writerow(target_row)