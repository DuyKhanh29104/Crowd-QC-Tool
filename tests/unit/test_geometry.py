import unittest
from backend.app.errors import DomainError
from backend.app.roi.geometry import validate_polygon,membership


class GeometryTests(unittest.TestCase):
    def test_concave_polygon_and_boundary(self):
        polygon=[[0,0],[10,0],[10,10],[5,5],[0,10]]
        validate_polygon(polygon,10,10)
        self.assertEqual(membership([5,7],polygon,True),"OUTSIDE")
        self.assertEqual(membership([5,4],polygon,True),"INSIDE")
        self.assertEqual(membership([0,0],polygon,True),"INSIDE")
        self.assertEqual(membership([5,4],polygon,False),"UNCERTAIN")

    def test_invalid_polygons(self):
        for polygon in [[[0,0],[1,1]],[[0,0],[10,10],[0,10],[10,0]],[[0,0],[10,0],[10,0]],[[0,0],[5,0],[10,0]],[[0,0],[float('nan'),1],[1,2]]]:
            with self.subTest(polygon=polygon),self.assertRaises(DomainError):
                validate_polygon(polygon,10,10)
