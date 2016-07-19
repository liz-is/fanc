import kaic
from kaic.data.registry import class_name_dict
import pytest
import os
import pybedtools


class TestAuto:
    def test_auto_identification(self, tmpdir):
        for class_name in ('Hic', 'AccessOptimisedHic', 'FragmentMappedReadPairs', 'Reads', 'GenomicTrack'):
            file_name = str(tmpdir) + '/{}.h5'.format(class_name)
            cls_ = class_name_dict[class_name]
            x = cls_(file_name=file_name, mode='w')
            x.close()

            x = kaic.load(file_name, mode='r')
            assert isinstance(x, cls_)
            x.close()

    def test_hic_based_auto_identification(self, tmpdir):
        with kaic.sample_hic() as hic:
            for class_name in ('ABDomains', 'ABDomainMatrix', 'ExpectedContacts', 'ObservedExpectedRatio',
                               'FoldChangeMatrix', 'ABDomains', 'PossibleContacts', 'RegionContactAverage',
                               'InsulationIndex', 'DirectionalityIndex'):
                file_name = str(tmpdir) + '/{}.h5'.format(class_name)
                cls_ = class_name_dict[class_name]
                x = cls_(hic, file_name=file_name, mode='w')
                x.close()

                x = kaic.load(file_name, mode='r')
                assert isinstance(x, cls_)
                x.close()

    def test_conversion(self, tmpdir):
        file_name = str(tmpdir) + '/x.hic'
        with kaic.sample_hic(file_name=file_name) as hic:
            # simulate old-style object
            hic.file.remove_node('/meta_information', recursive=True)

        with pytest.raises(ValueError):
            kaic.load(file_name, mode='r')

        hic = kaic.Hic(file_name)
        hic.close()

        hic = kaic.load(file_name, mode='r')
        hic.close()
        assert isinstance(hic, kaic.Hic)

    def test_bed(self):
        this_dir = os.path.dirname(os.path.realpath(__file__))
        bed_file = this_dir + '/test_auto/test.bed'

        bed = kaic.load(bed_file)
        assert isinstance(bed, pybedtools.BedTool)

        with pytest.raises(ValueError):
            foo_file = this_dir + '/test_auto/foo.txt'
            kaic.load(foo_file)
