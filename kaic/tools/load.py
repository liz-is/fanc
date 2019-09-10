import threading
import logging
import warnings
import os

from genomic_regions import load as gr_load
from ..registry import class_id_dict
import tables

logger = logging.getLogger(__name__)

kaic_access_lock = threading.Lock()


def load(file_name, *args, **kwargs):
    """
    Load a file into your current Python session.

    :func:`~load` is a magic function that replaces the need for importing
    files using different classes or functions. It "just works" for all
    objects generated by Kai-C (:class:`~Hic`, :class:`~ReadPairs`,
    :class:`~ABCompartmentMatrix`, ...), for compatible Hi-C files from
    `Cooler <https://github.com/mirnylab/cooler>`_ or
    `Juicer <https://github.com/aidenlab/juicer>`_, and most of the major
    file formats for genomic regions (BED, GFF, BigWig, Tabix, ...).

    Simply run

    .. code::

        o = kaic.load("/path/to/file")

    Depending on the file type, the returned object can be the instance of
    one (or more) of these classes:

    - :class:`~RegionBased` for genomic region formats (BED, GFF, ..., but
      also most Kai-C objects)
    - :class:`~RegionMatrixContainer` or :class:`~RegionPairsContainer` for
      read pair or matrix-based Kai-C objects, as well as Cooler and Juicer
      files
    - :class:`~pysam.AlignmentFile` for SAM/BAM files

    :param file_name: Path to file
    :param args: Positional arguments passed to the class/function that can
                 load the file
    :param kwargs: Keyword arguments passed to the class/function that can
                   load the file
    :return: object (:class:`~RegionBased`, :class:`~RegionMatrixContainer`,
             :class:`~RegionPairsContainer`, or :class:`~pysam.AlignmentFile`)
    """
    mode = kwargs.pop('mode', 'r')
    file_name = os.path.expanduser(file_name)

    try:
        logger.debug("Trying FileBased classes")

        f = tables.open_file(file_name, mode='r')
        try:
            classid = f.get_node('/', 'meta_information').meta_node.attrs['_classid']
            classid = classid.decode() if isinstance(classid, bytes) else classid
        finally:
            f.close()
        logger.debug("Class ID string: {}".format(classid))
        cls_ = class_id_dict[classid]
        logger.debug("Detected {}".format(cls_))
        return cls_(file_name=file_name, mode=mode, *args, **kwargs)
    except (tables.HDF5ExtError, AttributeError, KeyError) as e:
        logger.debug("Not a FileBased class (exception: {})".format(e))
        pass
    except OSError:
        logger.debug("Exact filename not found, might still be cooler uri")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from kaic.compatibility.cooler import is_cooler, CoolerHic
        if is_cooler(file_name):
            logger.debug("Cooler file detected")
            return CoolerHic(file_name, *args, **kwargs)
    except (ImportError, OSError, FileNotFoundError):
        pass

    from kaic.compatibility.juicer import JuicerHic, is_juicer
    if is_juicer(file_name):
        return JuicerHic(file_name, *args, **kwargs)

    return gr_load(file_name, *args, **kwargs)
