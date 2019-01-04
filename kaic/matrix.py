"""
TODO

update mappable region handling
update expected value handling
update normalisation handling

"""

import logging
import os

import numpy as np
import tables
from genomic_regions import RegionBased, GenomicRegion, as_region
import intervaltree

from .config import config
from .regions import LazyGenomicRegion, RegionsTable
from .tools.general import RareUpdateProgressBar, ranges, create_col_index, range_overlap
from .data.general import Maskable, MaskedTable

from collections import defaultdict
from future.utils import string_types

from bisect import bisect_right

logger = logging.getLogger(__name__)


class Edge(object):
    """
    A contact / an Edge between two genomic regions.

    .. attribute:: source

        The index of the "source" genomic region. By convention,
        source <= sink.

    .. attribute:: sink

        The index of the "sink" genomic region.

    .. attribute:: weight

        The weight or contact strength of the edge. Can, for
        example, be the number of reads mapping to a contact.
    """
    def __init__(self, source, sink, **kwargs):
        """
        :param source: The index of the "source" genomic region
                       or :class:`~Node` object.
        :param sink: The index of the "sink" genomic region
                     or :class:`~Node` object.
        :param data: The weight or of the edge or a dictionary with
                     other fields
        """
        self._source = source
        self._sink = sink
        self.field_names = []

        for key, value in kwargs.items():
            setattr(self, key.decode() if isinstance(key, bytes) else key, value)
            self.field_names.append(key)

    @property
    def source(self):
        try:
            return self._source.ix
        except AttributeError:
            return self._source

    @property
    def sink(self):
        try:
            return self._sink.ix
        except AttributeError:
            return self._sink

    @property
    def source_node(self):
        if isinstance(self._source, GenomicRegion):
            return self._source
        raise RuntimeError("Source not not provided during object initialization!")

    @property
    def sink_node(self):
        if isinstance(self._sink, GenomicRegion):
            return self._sink
        raise RuntimeError("Sink not not provided during object initialization!")

    def __repr__(self):
        base_info = "{}--{}".format(self.source, self.sink)
        for field in self.field_names:
            base_info += "; {}: {}".format(field, str(getattr(self, field)))
        return base_info


class LazyEdge(Edge):
    def __init__(self, row, nodes_table=None, auto_update=True):
        self.reserved = {'_row', '_nodes_table', 'auto_update', '_source_node', '_sink_node'}
        self._row = row
        self._nodes_table = nodes_table
        self.auto_update = auto_update
        self._source_node = None
        self._sink_node = None

    def _set_item(self, item, value):
        self._row[item] = value
        if self.auto_update:
            self.update()

    def __getattr__(self, item):
        if item == 'reserved' or item in self.reserved:
            return object.__getattribute__(self, item)
        try:
            value = self._row[item]
            value = value.decode() if isinstance(value, bytes) else value
            return value
        except KeyError:
            raise AttributeError("Attribute not supported (%s)" % str(item))

    def __setattr__(self, key, value):
        if key == 'reserved' or key in self.reserved:
            super(LazyEdge, self).__setattr__(key, value)
        else:
            self._row[key] = value
            if self.auto_update:
                self.update()

    def update(self):
        self._row.update()

    @property
    def source(self):
        return self._row['source']

    @property
    def sink(self):
        return self._row['sink']

    @property
    def source_node(self):
        if self._nodes_table is None:
            raise RuntimeError("Must set the _nodes_table attribute before calling this method!")

        if self._source_node is None:
            source_row = self._nodes_table[self.source]
            return LazyGenomicRegion(source_row)
        return self._source_node

    @property
    def sink_node(self):
        if self._nodes_table is None:
            raise RuntimeError("Must set the _nodes_table attribute before calling this method!")

        if self._sink_node is None:
            sink_row = self._nodes_table[self.sink]
            return LazyGenomicRegion(sink_row)
        return self._sink_node

    def __repr__(self):
        base_info = "{}--{}".format(self.source, self.sink)
        return base_info


def as_edge(edge):
    if isinstance(edge, Edge):
        return edge

    try:
        return Edge(**edge)
    except TypeError:
        pass

    if isinstance(edge, tuple) and len(edge) > 1 and \
            isinstance(edge[0], GenomicRegion) and isinstance(edge[1], GenomicRegion):
        try:
            source, sink = edge[0].ix, edge[1].ix
            if len(edge) > 2:
                return Edge(source, sink, weight=edge[2])
            return Edge(source, sink)
        except AttributeError:
            pass

    try:
        source, sink = edge[0], edge[1]
        try:
            weight = edge[2]
        except IndexError:
            weight = 1

        return Edge(source, sink, weight=weight)
    except (TypeError, IndexError):
        pass

    try:
        weight = getattr(edge, 'weight', None)
        if weight is not None:
            return Edge(edge.source, edge.sink, weight=weight)
        return Edge(edge.source, edge.sink)
    except AttributeError:
        pass

    raise ValueError("{} of type {} not recognised as edge "
                     "/ contact!".format(edge, type(edge)))


class RegionPairsContainer(RegionBased):

    def __init__(self):
        RegionBased.__init__(self)

    def _add_edge(self, edge, *args, **kwargs):
        raise NotImplementedError("Subclass must override this function")

    def _edges_iter(self, *args, **kwargs):
        raise NotImplementedError("Subclass must implement _edges_iter "
                                  "to enable iterating over edges!")

    def _edges_subset(self, key=None, *args, **kwargs):
        raise NotImplementedError("Subclass must implement _edges_subset "
                                  "to enable iterating over edge subsets!")

    def _edges_length(self):
        return sum(1 for _ in self.edges)

    def _edges_getitem(self, item, *args, **kwargs):
        raise NotImplementedError("Subclass must implement _edges_getitem "
                                  "to enable getting specific edges!")

    def _key_to_regions(self, key, *args, **kwargs):
        if isinstance(key, tuple):
            if len(key) == 2:
                row_key, col_key = key
            else:
                raise ValueError("Cannot retrieve edge table rows using key {}".format(key))
        else:
            row_key = key
            col_key = slice(0, len(self.regions), 1)

        return self.regions(row_key, *args, **kwargs), self.regions(col_key, *args, **kwargs)

    def _min_max_region_ix(self, regions):
        min_ix = len(self.regions)
        max_ix = 0
        for region in regions:
            min_ix = min(min_ix, region.ix)
            max_ix = max(max_ix, region.ix)
        return min_ix, max_ix

    def __len__(self):
        return self._edges_length()

    def __getitem__(self, item):
        return self._edges_getitem(item)

    def __iter__(self):
        return self.edges()

    def add_contact(self, contact, *args, **kwargs):
        return self.add_edge(contact, *args, **kwargs)

    def add_edge(self, edge, check_nodes_exist=True, *args, **kwargs):
        """
        Add an edge to this object.

        :param edge: :class:`~Edge`, dict with at least the
                     attributes source and sink, optionally weight,
                     or a list of length 2 (source, sink) or 3
                     (source, sink, weight).
        :param check_nodes_exist: Make sure that there are nodes
                                  that match source and sink indexes
        """
        edge = as_edge(edge)

        if check_nodes_exist:
            n_regions = len(self.regions)
            if edge.source >= n_regions or edge.sink >= n_regions:
                raise ValueError("Node index exceeds number of nodes in object")

        self._add_edge(edge, *args, **kwargs)

    def add_edges(self, edges, *args, **kwargs):
        """
        Bulk-add edges from a list.

        :param edges: List (or iterator) of edges. See
                      :func:`~RegionMatrixTable.add_edge`
                      for details
        """
        for edge in edges:
            self.add_edge(edge, *args, **kwargs)

    def add_contacts(self, contacts, *args, **kwargs):
        return self.add_edges(contacts, *args, **kwargs)

    @property
    def edges(self):
        """
        Iterate over :class:`~Edge` objects.

        :return: Iterator over :class:`~Edge`
        """

        class EdgeIter(object):
            def __init__(self, this):
                self._regions_pairs = this

            def __getitem__(self, item):
                return self._regions_pairs._edges_getitem(item)

            def __iter__(self):
                return self._regions_pairs._edges_iter()

            def __call__(self, key=None, *args, **kwargs):
                if key is None:
                    return self._regions_pairs._edges_iter(*args, **kwargs)
                else:
                    return self._regions_pairs.edge_subset(key, *args, **kwargs)

            def __len__(self):
                return self._regions_pairs._edges_length()

        return EdgeIter(self)

    def edge_subset(self, key=None, *args, **kwargs):
        """
        Get a subset of edges.

        :param key: Possible key types are:

                    Region types

                    - Node: Only the ix of this node will be used for
                      identification
                    - GenomicRegion: self-explanatory
                    - str: key is assumed to describe a genomic region
                      of the form: <chromosome>[:<start>-<end>:[<strand>]],
                      e.g.: 'chr1:1000-54232:+'

                    Node types

                    - int: node index
                    - slice: node range

                    List types

                    - list: This key type allows for a combination of all
                      of the above key types - the corresponding matrix
                      will be concatenated


                    If the key is a 2-tuple, each entry will be treated as the
                    row and column key, respectively,
                    e.g.: 'chr1:0-1000, chr4:2300-3000' will extract the Hi-C
                    map of the relevant regions between chromosomes 1 and 4.
        :return: generator (:class:`~Edge`)
        """
        return self._edges_subset(key, *args, **kwargs)

    def mappable(self):
        """
        Get the mappability vector of this matrix.
        """
        logger.debug("Calculating mappability...")

        mappable = np.zeros(len(self.regions), dtype=bool)
        with RareUpdateProgressBar(max_value=len(self.edges), silent=config.hide_progressbars) as pb:
            for i, edge in enumerate(self.edges(lazy=True)):
                mappable[edge.source] = True
                mappable[edge.sink] = True
                pb.update(i)
        return mappable


class RegionMatrixContainer(RegionPairsContainer):
    def __init__(self):
        RegionPairsContainer.__init__(self)
        self._default_value = 0.0
        self._default_score_field = 'weight'

    def matrix_entries(self, key=None, score_field=None, bias_field='bias',
                       *args, **kwargs):
        if score_field is None:
            score_field = self._default_score_field

        for edge in self.edges(key, *args, **kwargs):
            yield (edge.source, edge.sink, getattr(edge, score_field, self._default_value))

    def matrix(self, key=None,
               score_field=None, default_value=None,
               mask_invalid=False, _mappable=None):

        if score_field is None:
            score_field = self._default_score_field

        if default_value is None:
            default_value = self._default_value

        row_regions, col_regions = self._key_to_regions(key)
        row_regions, col_regions = list(row_regions), list(col_regions)
        row_offset = row_regions[0].ix
        col_offset = col_regions[0].ix

        row_biases = np.array([getattr(r, 'bias', 1.0) for r in row_regions])
        col_biases = np.array([getattr(r, 'bias', 1.0) for r in col_regions])

        m = np.full((len(row_regions), len(col_regions)), default_value)

        for source, sink, weight in self.matrix_entries(key, score_field):
            ir = source - row_offset
            jr = sink - col_offset
            if 0 <= ir < m.shape[0] and 0 <= jr < m.shape[1]:
                m[ir, jr] = weight

            ir = sink - row_offset
            jr = source - col_offset
            if 0 <= ir < m.shape[0] and 0 <= jr < m.shape[1]:
                m[ir, jr] = weight

        # remove matrix biases
        m / row_biases[:, None] / col_biases

        if mask_invalid:
            mask = np.zeros(m.shape, dtype=bool)
            for row_region in row_regions:
                valid = getattr(row_region, 'valid', True)
                if not valid:
                    mask[row_region.ix - - row_offset] = True

            for col_region in col_regions:
                valid = getattr(col_region, 'valid', True)
                if not valid:
                    mask[col_region.ix - - col_offset] = True

            m = np.ma.MaskedArray(m, mask=mask)

        if (isinstance(key, tuple) and len(key) == 2 and
                isinstance(key[0], int) and isinstance(key[1], int)):
            return m[0, 0]

        return RegionMatrix(m, row_regions=row_regions, col_regions=col_regions)


class RegionPairsTable(RegionPairsContainer, Maskable, RegionsTable):

    _classid = 'REGIONPAIRSTABLE'

    def __init__(self, file_name=None, mode='a', tmpdir=None,
                 additional_region_fields=None, additional_edge_fields=None,
                 partitioning_strategy='chromosome',
                 _table_name_regions='regions', _table_name_edges='edges',
                 _edge_buffer_size=1000000):

        """
        Initialize a :class:`~RegionPairsTable` object.

        :param file_name: Path to a save file
        :param mode: File mode to open underlying file
        :param additional_fields: Additional fields (in PyTables notation) associated with
                                  edge data, e.g. {'weight': tables.Float32Col()}
        :param _table_name_regions: (Internal) name of the HDF5 node for regions
        :param _table_name_edges: (Internal) name of the HDF5 node for edges
        :param _edge_buffer_size: (Internal) size of edge / contact buffer
        """

        # private variables
        self._edges_dirty = False
        self._edge_index_dirty = False
        self._mappability_dirty = False
        self._partitioning_strategy = partitioning_strategy

        file_exists = False
        if file_name is not None:
            file_name = os.path.expanduser(file_name)
            if os.path.exists(file_name):
                file_exists = True

        # initialise inherited objects
        RegionsTable.__init__(self, file_name=file_name, _table_name_regions=_table_name_regions,
                              mode=mode, tmpdir=tmpdir, additional_fields=additional_region_fields)
        Maskable.__init__(self, self.file)

        self._edge_table_dict = dict()
        if file_exists:
            # retrieve edge tables and partitions
            self._edges = self.file.get_node('/', _table_name_edges)
            self._partition_breaks = getattr(self.meta, 'partition_breaks', None)
            if self._partition_breaks is None:
                self._update_partitions()
        else:
            self._edges = self.file.create_group('/', _table_name_edges)

            basic_fields = {
                'source': tables.Int32Col(pos=0),
                'sink': tables.Int32Col(pos=1),
            }
            if additional_edge_fields is not None:
                if not isinstance(additional_edge_fields, dict) and issubclass(additional_edge_fields,
                                                                          tables.IsDescription):
                    # IsDescription subclass case
                    additional_edge_fields = additional_edge_fields.columns

                current = len(basic_fields)
                for key, value in sorted(additional_edge_fields.items(), key=lambda x: x[1]._v_pos):
                    if key not in basic_fields:
                        if value._v_pos is not None:
                            value._v_pos = current
                            current += 1
                        basic_fields[key] = value

            self._partition_breaks = None
            self._update_partitions()

            self._create_edge_table(0, 0, fields=basic_fields)

        # update edge table dict
        self._edge_table_dict = dict()
        for edge_table in self._edges:
            self._edge_table_dict[(edge_table.attrs['source_partition'],
                                   edge_table.attrs['sink_partition'])] = edge_table

        # update field names
        self._source_field_ix = 0
        self._sink_field_ix = 0
        self.field_names = []
        self._field_names_dict = dict()
        self._edge_field_defaults = dict()
        self._update_field_names()

        # set up edge buffer
        self._edge_buffer = defaultdict(list)
        self._edge_buffer_size = _edge_buffer_size

    def _flush_table_edge_buffer(self):
        for (source_partition, sink_partition), records in self._edge_buffer.items():
            if not (source_partition, sink_partition) in self._edge_table_dict:
                self._create_edge_table(source_partition, sink_partition)
            table = self._edge_table_dict[(source_partition, sink_partition)]
            table.append(records)
        self._edge_buffer = defaultdict(list)

    def _flush_regions(self):
        if self._regions_dirty:
            RegionsTable._flush_regions(self)
            self._update_partitions()

    def _flush_edges(self, silent=config.hide_progressbars):
        if self._edges_dirty:
            if len(self._edge_buffer) > 0:
                logger.debug("Adding buffered edges...")
                self._flush_table_edge_buffer()

            if self._edge_index_dirty:
                logger.debug("Updating mask indices...")

            with RareUpdateProgressBar(max_value=sum(1 for _ in self._edges), silent=silent) as pb:
                for i, edge_table in enumerate(self._edges):
                    edge_table.flush(update_index=self._edge_index_dirty, log_progress=False)
                    pb.update(i)
                self._edge_index_dirty = False
                self._edges_dirty = False

        if self._mappability_dirty:
            self.meta['has_mappability_info'] = False
            self.mappable()
            self._mappability_dirty = False

    def flush(self, silent=config.hide_progressbars):
        """
        Write data to file and flush buffers.

        :param silent: do not print flush progress
        """
        self._flush_regions()
        self._flush_edges(silent=silent)

    def _update_partitions(self):
        partition_breaks = []

        if self._partitioning_strategy == 'chromosome':
            previous_chromosome = None
            for i, region in enumerate(self.regions(lazy=True)):
                if region.chromosome != previous_chromosome and previous_chromosome is not None:
                    partition_breaks.append(i)
                previous_chromosome = region.chromosome
        elif isinstance(self._partitioning_strategy, int):
            n_regions = len(self.regions)
            for i in range(self._partitioning_strategy, n_regions, self._partitioning_strategy):
                partition_breaks.append(i)

        self._partition_breaks = partition_breaks
        try:
            self.meta['partition_breaks'] = partition_breaks
        except tables.FileModeError:
            pass

    def _update_field_names(self):
        """
        Set internal object variables related to edge table field names.
        """
        edge_table = self._edge_table_dict[(0, 0)]

        # update field names
        self._source_field_ix = 0
        self._sink_field_ix = 0
        self.field_names = []
        self._field_names_dict = dict()
        self._edge_field_defaults = dict()
        for i, name in enumerate(edge_table.colnames):
            if not name.startswith("_"):
                self.field_names.append(name)
            if name == 'source':
                self._source_field_ix = i
            if name == 'sink':
                self._sink_field_ix = i
            self._field_names_dict[name] = i
            self._edge_field_defaults[name] = edge_table.coldescrs[name].dflt

    def _create_edge_table(self, source_partition, sink_partition, fields=None):
        """
        Create and register an edge table for a partition combination.
        """
        if fields is None:
            fields = self._edge_table_dict[(0, 0)].coldescrs

        if (source_partition, sink_partition) in self._edge_table_dict:
            return self._edge_table_dict[(source_partition, sink_partition)]

        edge_table = MaskedTable(self._edges,
                                 'chrpair_' + str(source_partition) + '_' + str(sink_partition),
                                 fields, ignore_reserved_fields=True)
        edge_table.attrs['source_partition'] = source_partition
        edge_table.attrs['sink_partition'] = sink_partition

        # index
        create_col_index(edge_table.cols.source)
        create_col_index(edge_table.cols.sink)

        self._edge_table_dict[(source_partition, sink_partition)] = edge_table
        return edge_table

    def _get_edge_table_tuple(self, source, sink):
        if source > sink:
            source, sink = sink, source

        source_partition = self._get_partition_ix(source)
        sink_partition = self._get_partition_ix(sink)

        return source_partition, sink_partition

    def _add_edge(self, edge, row=None, replace=False):
        """
        Add an edge to an internal edge table.
        """
        source, sink = edge.source, edge.sink
        if source > sink:
            source, sink = sink, source

        if row is None:
            record = [None] * len(self._field_names_dict)
            for name, ix in self._field_names_dict.items():
                try:
                    record[ix] = getattr(edge, name)
                except AttributeError:
                    record[ix] = self._edge_field_defaults[name]
            record[self._field_names_dict['source']] = source
            record[self._field_names_dict['sink']] = sink

            self._add_edge_from_tuple(record)
        else:
            row['source'] = source
            row['sink'] = sink
            for name in self.field_names:
                if not name == 'source' and not name == 'sink':
                    try:
                        value = getattr(edge, name)
                        if replace:
                            row[name] = value
                        else:
                            row[name] += value
                    except AttributeError:
                        pass
            row.update()

        self._edges_dirty = True
        self._edge_index_dirty = True

    def _add_edge_from_tuple(self, edge):
        source = edge[self._source_field_ix]
        sink = edge[self._sink_field_ix]
        if source > sink:
            source, sink = sink, source
        source_partition, sink_partition = self._get_edge_table_tuple(source, sink)

        self._edge_buffer[(source_partition, sink_partition)].append(tuple(edge))
        if sum(len(records) for records in self._edge_buffer.values()) > self._edge_buffer_size:
            self._flush_table_edge_buffer()

        self._edges_dirty = True
        self._edge_index_dirty = True

    def add_edges(self, edges, *args, **kwargs):
        if self._regions_dirty:
            self._flush_regions()
        RegionPairsContainer.add_edges(self, edges, *args, **kwargs)
        self._flush_edges()

    def _get_partition_ix(self, region_ix):
        """
        Bisect the partition table to get the partition index for a region index.
        """
        return bisect_right(self._partition_breaks, region_ix)

    def _is_partition_covered(self, partition_ix, region_ix_start, region_ix_end):
        try:
            partition_end = self._partition_breaks[partition_ix]
        except IndexError:
            partition_end = len(self.regions)
        if partition_ix > 0:
            partition_start = self._partition_breaks[partition_ix - 1]
        else:
            partition_start = 0

        if region_ix_start <= partition_start and region_ix_end >= partition_end - 1:
            return True
        return False

    def _edge_subset_rows(self, key):
        row_regions, col_regions = self._key_to_regions(key, lazy=True)

        return self._edge_subset_rows_from_regions(
            row_regions, col_regions
        )

    def _edge_subset_rows_from_regions(self, row_regions, col_regions):
        row_start, row_end = self._min_max_region_ix(row_regions)
        col_start, col_end = self._min_max_region_ix(col_regions)

        row_partition_start = self._get_partition_ix(row_start)
        row_partition_end = self._get_partition_ix(row_end)
        col_partition_start = self._get_partition_ix(col_start)
        col_partition_end = self._get_partition_ix(col_end)

        for i in range(row_partition_start, row_partition_end + 1):
            for j in range(col_partition_start, col_partition_end + 1):
                if j < i:
                    i, j = j, i
                edge_table = self._edge_table_dict[(i, j)]

                # if we need to get all regions in a table, return the whole thing
                if (self._is_partition_covered(i, row_start, row_end) and
                        self._is_partition_covered(j, col_start, col_end)):
                    for row in edge_table:
                        yield row

                # otherwise only return the subset defined by the respective indices
                else:
                    condition = "(%d < source) & (source < %d) & (% d < sink) & (sink < %d)"
                    condition1 = condition % (row_start - 1, row_end + 1, col_start - 1, col_end + 1)
                    condition2 = condition % (col_start - 1, col_end + 1, row_start - 1, row_end + 1)

                    if row_start > col_start:
                        condition1, condition2 = condition2, condition1

                    overlap = range_overlap(row_start, row_end, col_start, col_end)

                    for edge_row in edge_table.where(condition1):
                        yield edge_row

                    for edge_row in edge_table.where(condition2):
                        if overlap is not None:
                            if (overlap[0] <= edge_row['source'] <= overlap[1]) and (
                                    overlap[0] <= edge_row['sink'] <= overlap[1]):
                                continue

                        yield edge_row

    def _row_to_edge(self, row, lazy=False, auto_update=True, **kwargs):
        if not lazy:
            source = row["source"]
            sink = row["sink"]
            d = dict()
            for field in self.field_names:
                if field != 'source' and field != 'sink':
                    value = row[field]
                    value = value.decode() if isinstance(value, bytes) else value
                    d[field] = value

            source_node = self.regions[source]
            sink_node = self.regions[sink]
            return Edge(source_node, sink_node, **d)
        else:
            return LazyEdge(row, self._regions, auto_update=auto_update)

    def _edges_subset(self, key=None, *args, **kwargs):
        for row in self._edge_subset_rows(key):
            yield self._row_to_edge(row, *args, **kwargs)

    def _edges_iter(self, *args, **kwargs):
        for i in range(0, len(self._partition_breaks) + 1):
            for j in range(i, len(self._partition_breaks) + 1):
                if (i, j) in self._edge_table_dict:
                    for row in self._edge_table_dict[(i, j)]:
                        yield self._row_to_edge(row, *args, **kwargs)

    def _edges_length(self):
        s = 0
        for edge_table in self._edge_table_dict.values():
            s += len(edge_table)
        return s

    def _edges_getitem(self, item, *args, **kwargs):
        result = list(self.edges(item))
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], int) and isinstance(item[1], int):
            return result[0]
        return result


class RegionMatrixTable(RegionMatrixContainer, RegionPairsTable):

    _classid = 'REGIONMATRIXTABLE'

    def __init__(self, file_name=None, mode='a', tmpdir=None,
                 partitioning_strategy='chromosome',
                 _table_name_regions='regions', _table_name_edges='edges',
                 _edge_buffer_size=1000000):
        RegionPairsTable.__init__(self,
                                  file_name=file_name, mode=mode, tmpdir=tmpdir,
                                  additional_region_fields={
                                      'valid': tables.BoolCol(dflt=True),
                                      'bias': tables.Float64Col(dflt=1.0),
                                  },
                                  additional_edge_fields={
                                      'weight': tables.Float64Col()
                                  },
                                  partitioning_strategy=partitioning_strategy,
                                  _table_name_regions=_table_name_regions,
                                  _table_name_edges=_table_name_edges,
                                  _edge_buffer_size=_edge_buffer_size)
        RegionMatrixContainer.__init__(self)


class RegionMatrix(np.ndarray):
    def __new__(cls, input_matrix, col_regions=None, row_regions=None):
        obj = np.asarray(input_matrix).view(cls)
        obj._row_region_trees = None
        obj._col_region_trees = None
        obj.set_col_regions(col_regions)
        obj.set_row_regions(row_regions)
        return obj

    def _interval_tree_regions(self, regions):
        intervals = defaultdict(list)
        for i, region in enumerate(regions):
            interval = intervaltree.Interval(region.start - 1, region.end,
                                             data=i)
            intervals[region.chromosome].append(interval)

        interval_trees = {chromosome: intervaltree.IntervalTree(intervals)
                          for chromosome, intervals in intervals.items()}
        return interval_trees

    def set_row_regions(self, regions):
        self.row_regions = regions
        if regions is not None:
            self._row_region_trees = self._interval_tree_regions(regions)
        else:
            self._row_region_trees = None

    def set_col_regions(self, regions):
        self.col_regions = regions
        if regions is not None:
            self._col_region_trees = self._interval_tree_regions(regions)
        else:
            self._col_region_trees = None

    def __array_finalize__(self, obj):
        if obj is None:
            return

        self.set_row_regions(getattr(obj, 'row_regions', None))
        self.set_col_regions(getattr(obj, 'col_regions', None))

    def __setitem__(self, key, item):
        self._setitem = True
        try:
            if isinstance(self, np.ma.core.MaskedArray):
                out = np.ma.MaskedArray.__setitem__(self, key, item)
            else:
                out = np.ndarray.__setitem__(self, key, item)
        finally:
            self._setitem = False

    def __getitem__(self, index):
        self._getitem = True

        # convert string types into region indexes
        if isinstance(index, tuple):
            if len(index) == 2:
                row_key = self._convert_key(
                    index[0],
                    self._row_region_trees if hasattr(self, '_row_region_trees') else None
                )
                col_key = self._convert_key(
                    index[1],
                    self._col_region_trees if hasattr(self, '_col_region_trees') else None
                )
                index = (row_key, col_key)
            elif len(index) == 1:
                row_key = self._convert_key(index[0], self._row_region_trees)
                col_key = slice(0, len(self.col_regions), 1)
                index = (row_key, )
            else:
                col_key = slice(0, len(self.col_regions), 1)
                row_key = index
                index = row_key
        else:
            row_key = self._convert_key(index, self._row_region_trees)
            try:
                col_key = slice(0, len(self.col_regions), 1)
            except TypeError:
                col_key = None
            index = row_key

        try:
            if isinstance(self, np.ma.core.MaskedArray):
                out = np.ma.MaskedArray.__getitem__(self, index)
            else:
                out = np.ndarray.__getitem__(self, index)
        finally:
            self._getitem = False

        if not isinstance(out, np.ndarray):
            return out

        # get regions
        try:
            row_regions = self.row_regions[row_key]
        except TypeError:
            row_regions = None

        try:
            col_regions = self.col_regions[col_key]
        except TypeError:
            col_regions = None

        if isinstance(row_regions, GenomicRegion):
            out.row_regions = [row_regions]
        else:
            out.row_regions = row_regions

        if isinstance(col_regions, GenomicRegion):
            out.col_regions = [col_regions]
        else:
            out.col_regions = col_regions

        return out

    def __getslice__(self, start, stop):
        return self.__getitem__(slice(start, stop))

    def _convert_key(self, key, region_trees):
        if isinstance(key, string_types):
            key = GenomicRegion.from_string(key)

        if isinstance(key, GenomicRegion):
            start = None
            stop = None
            try:
                key_start = 0 if key.start is None else max(0, key.start - 1)
                key_end = key.end
                for interval in region_trees[key.chromosome][key_start:key_end]:
                    i = interval.data
                    start = min(i, start) if start is not None else i
                    stop = max(i + 1, stop) if stop is not None else i + 1
            except KeyError:
                raise ValueError("Requested chromosome {} was not "
                                 "found in this matrix.".format(key.chromosome))

            if start is None or stop is None:
                raise ValueError("Requested region {} was not found in this matrix.".format(key))

            return slice(start, stop, 1)
        return key