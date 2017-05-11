# coding: utf-8
# Copyright (c) Pymatgen Development Team.
# Distributed under the terms of the MIT License.

from __future__ import division, print_function, unicode_literals, \
    absolute_import

from scipy.linalg import polar
import numpy as np
import itertools
import warnings
import collections
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.core.operations import SymmOp
from pymatgen.core.lattice import Lattice

"""
This module provides a base class for tensor-like objects and methods for
basic tensor manipulation.  It also provides a class, SquareTensor,
that provides basic methods for creating and manipulating rank 2 tensors
"""

__author__ = "Maarten de Jong"
__copyright__ = "Copyright 2012, The Materials Project"
__credits__ = ("Joseph Montoya, Shyam Dwaraknath, Wei Chen, "
               "Mark Asta, Anubhav Jain, Terence Lew")
__version__ = "1.0"
__maintainer__ = "Joseph Montoya"
__email__ = "montoyjh@lbl.gov"
__status__ = "Development"
__date__ = "March 22, 2012"

voigt_map = [(0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1)]
reverse_voigt_map = np.array([[0, 5, 4],
                              [5, 1, 3],
                              [4, 3, 2]])


class Tensor(np.ndarray):
    """
    Base class for doing useful general operations on Nth order tensors,
    without restrictions on the type (stress, elastic, strain, piezo, etc.)
    """

    def __new__(cls, input_array, vscale=None, check_rank=None):
        """
        Create a Tensor object.  Note that the constructor uses __new__
        rather than __init__ according to the standard method of
        subclassing numpy ndarrays.

        Args:
            input_array: (array-like with shape 3^N): array-like representing
                a tensor quantity in standard (i. e. non-voigt) notation
            vscale: (N x M array-like): a matrix corresponding
                to the coefficients of the voigt-notation tensor
        """
        obj = np.asarray(input_array).view(cls)
        obj.rank = len(obj.shape)

        if check_rank and check_rank != obj.rank:
            raise ValueError("{} input must be rank {}".format(
                obj.__class__.__name__, check_rank))

        vshape = tuple([3] * (obj.rank % 2) + [6] * (obj.rank // 2))
        obj._vscale = np.ones(vshape)
        if vscale is not None:
            obj._vscale = vscale
        if obj._vscale.shape != vshape:
            raise ValueError("Voigt scaling matrix must be the shape of the "
                             "voigt notation matrix or vector.")
        if not all([i == 3 for i in obj.shape]):
            raise ValueError("Pymatgen only supports 3-dimensional tensors, "
                             "and default tensor constructor uses standard "
                             "notation.  To construct from voigt notation, use"
                             " {}.from_voigt".format(obj.__class__.__name__))
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.rank = getattr(obj, 'rank', None)
        self._vscale = getattr(obj, '_vscale', None)
        self._vdict = getattr(obj, '_vdict', None)

    def __array_wrap__(self, obj):
        """
        Overrides __array_wrap__ methods in ndarray superclass to avoid errors
        associated with functions that return scalar values
        """

        if len(obj.shape) == 0:
            return obj[()]
        else:
            return np.ndarray.__array_wrap__(self, obj)

    def __hash__(self):
        """
        define a hash function, since numpy arrays
        have their own __eq__ method
        """
        return hash(self.tostring())

    def __repr__(self):
        return "{}({})".format(self.__class__.__name__,
                               self.__str__())

    def zeroed(self, tol=1e-3):
        """
        returns the matrix with all entries below a certain threshold
        (i.e. tol) set to zero
        """
        new_tensor = self.copy()
        new_tensor[abs(new_tensor) < tol] = 0
        return new_tensor

    def transform(self, symm_op):
        """
        Applies a transformation (via a symmetry operation) to a tensor. 

        Args:
            symm_op (SymmOp): a symmetry operation to apply to the tensor
        """
        return self.__class__(symm_op.transform_tensor(self))

    def rotate(self, matrix, tol=1e-3):
        """
        Applies a rotation directly, and tests input matrix to ensure a valid
        rotation.

        Args:
            matrix (3x3 array-like): rotation matrix to be applied to tensor
            tol (float): tolerance for testing rotation matrix validity
        """
        matrix = SquareTensor(matrix)
        if not matrix.is_rotation(tol):
            raise ValueError("Rotation matrix is not valid.")
        sop = SymmOp.from_rotation_and_translation(matrix,
                                                   [0., 0., 0.])
        return self.transform(sop)

    @property
    def symmetrized(self):
        """
        Returns a generally symmetrized tensor, calculated by taking 
        the sum of the tensor and its transpose with respect to all 
        possible permutations of indices
        """
        perms = list(itertools.permutations(range(self.rank)))
        return sum([np.transpose(self, ind) for ind in perms]) / len(perms)

    @property
    def voigt_symmetrized(self):
        """
        Returns a "voigt"-symmetrized tensor, i. e. a voigt-notation
        tensor such that it is invariant wrt permutation of indices
        """
        if not (self.rank % 2 == 0 and self.rank > 2):
            raise ValueError("V-symmetrization requires rank even and > 2")

        v = self.voigt
        perms = list(itertools.permutations(range(len(v.shape))))
        new_v = sum([np.transpose(v, ind) for ind in perms]) / len(perms)
        return self.__class__.from_voigt(new_v)

    def is_symmetric(self, tol=1e-5):
        """
        Tests whether a tensor is symmetric or not based on the residual
        with its symmetric part, from self.symmetrized

        Args:
            tol (float): tolerance to test for symmetry
        """
        return (self - self.symmetrized < tol).all()

    def fit_to_structure(self, structure, symprec=0.1):
        """
        Returns a tensor that is invariant with respect to symmetry
        operations corresponding to a structure

        Args: 
            structure (Structure): structure from which to generate 
                symmetry operations
            symprec (float): symmetry tolerance for the Spacegroup Analyzer
                used to generate the symmetry operations
        """
        sga = SpacegroupAnalyzer(structure, symprec)
        symm_ops = sga.get_symmetry_operations(cartesian=True)
        return sum([self.transform(symm_op)
                    for symm_op in symm_ops]) / len(symm_ops)

    def is_fit_to_structure(self, structure, tol=1e-2):
        """
        Tests whether a tensor is invariant with respect to the
        symmetry operations of a particular structure by testing
        whether the residual of the symmetric portion is below a 
        tolerance
        
        Args:
            structure (Structure): structure to be fit to
            tol (float): tolerance for symmetry testing
        """
        return (self - self.fit_to_structure(structure) < tol).all()

    @property
    def voigt(self):
        """
        Returns the tensor in Voigt notation
        """
        v_matrix = np.zeros(self._vscale.shape, dtype=self.dtype)
        this_voigt_map = self.get_voigt_dict(self.rank)
        for ind in this_voigt_map:
            v_matrix[this_voigt_map[ind]] = self[ind]
        if not self.is_voigt_symmetric():
            warnings.warn("Tensor is not symmetric, information may "
                          "be lost in voigt conversion.")
        return v_matrix * self._vscale

    def is_voigt_symmetric(self, tol=1e-6):
        """
        Tests symmetry of tensor to that necessary for voigt-conversion
        by grouping indices into pairs and constructing a sequence of
        possible permutations to be used in a tensor transpose
        """
        transpose_pieces = [[[0 for i in range(self.rank % 2)]]]
        transpose_pieces += [[range(j, j + 2)] for j in
                             range(self.rank % 2, self.rank, 2)]
        for n in range(self.rank % 2, len(transpose_pieces)):
            if len(transpose_pieces[n][0]) == 2:
                transpose_pieces[n] += [transpose_pieces[n][0][::-1]]
        for trans_seq in itertools.product(*transpose_pieces):
            trans_seq = list(itertools.chain(*trans_seq))
            if (self - self.transpose(trans_seq) > tol).any():
                return False
        return True

    @staticmethod
    def get_voigt_dict(rank):
        """
        Returns a dictionary that maps indices in the tensor to those
        in a voigt representation based on input rank

        Args:
            rank (int): Tensor rank to generate the voigt map
        """
        vdict = {}
        for ind in itertools.product(*[range(3)] * rank):
            v_ind = ind[:rank % 2]
            for j in range(rank // 2):
                pos = rank % 2 + 2 * j
                v_ind += (reverse_voigt_map[ind[pos:pos + 2]],)
            vdict[ind] = v_ind
        return vdict

    @classmethod
    def from_voigt(cls, voigt_input):
        """
        Constructor based on the voigt notation vector or matrix.

        Args: 
            voigt_input (array-like): voigt input for a given tensor
        """
        voigt_input = np.array(voigt_input)
        rank = sum(voigt_input.shape) // 3
        t = cls(np.zeros([3] * rank))
        if voigt_input.shape != t._vscale.shape:
            raise ValueError("Invalid shape for voigt matrix")
        voigt_input = voigt_input / t._vscale
        this_voigt_map = t.get_voigt_dict(rank)
        for ind in this_voigt_map:
            t[ind] = voigt_input[this_voigt_map[ind]]
        return cls(t)

    def convert_to_ieee(self, structure):
        """
        Given a structure associated with a tensor, attempts a
        calculation of the tensor in IEEE format according to
        the 1987 IEEE standards.

        Args:
            structure (Structure): a structure associated with the
                tensor to be converted to the IEEE standard
        """

        def get_uvec(v):
            """ Gets a unit vector parallel to input vector"""
            l = np.linalg.norm(v)
            if l < 1e-8:
                return v
            return v / l

        # Check conventional setting:
        sga = SpacegroupAnalyzer(structure)
        dataset = sga.get_symmetry_dataset()
        trans_mat = dataset['transformation_matrix']
        conv_latt = Lattice(np.transpose(np.dot(np.transpose(
            structure.lattice.matrix), np.linalg.inv(trans_mat))))
        xtal_sys = sga.get_crystal_system()

        vecs = conv_latt.matrix
        lengths = np.array(conv_latt.abc)
        angles = np.array(conv_latt.angles)
        rotation = np.zeros((3, 3))

        # IEEE rules: a,b,c || x1,x2,x3
        if xtal_sys == "cubic":
            rotation = [vecs[i] / lengths[i] for i in range(3)]

        # IEEE rules: a=b in length; c,a || x3, x1
        elif xtal_sys == "tetragonal":
            rotation = np.array([vec / mag for (mag, vec) in
                                 sorted(zip(lengths, vecs),
                                        key=lambda x: x[0])])
            if abs(lengths[2] - lengths[1]) < abs(lengths[1] - lengths[0]):
                rotation[0], rotation[2] = rotation[2], rotation[0].copy()
            rotation[1] = get_uvec(np.cross(rotation[2], rotation[0]))

        # IEEE rules: c<a<b; c,a || x3,x1
        elif xtal_sys == "orthorhombic":
            rotation = [vec / mag for (mag, vec) in sorted(zip(lengths, vecs))]
            rotation = np.roll(rotation, 2, axis=0)

        # IEEE rules: c,a || x3,x1, c is threefold axis
        # Note this also includes rhombohedral crystal systems
        elif xtal_sys in ("trigonal", "hexagonal"):
            # find threefold axis:
            tf_index = np.argmin(abs(angles - 120.))
            non_tf_mask = np.logical_not(angles == angles[tf_index])
            rotation[2] = get_uvec(vecs[tf_index])
            rotation[0] = get_uvec(vecs[non_tf_mask][0])
            rotation[1] = get_uvec(np.cross(rotation[2], rotation[0]))

        # IEEE rules: b,c || x2,x3; alpha=beta=90, c<a
        elif xtal_sys == "monoclinic":
            # Find unique axis
            u_index = np.argmax(abs(angles - 90.))
            n_umask = np.logical_not(angles == angles[u_index])
            rotation[1] = get_uvec(vecs[u_index])
            # Shorter of remaining lattice vectors for c axis
            c = [vec / mag for (mag, vec) in
                 sorted(zip(lengths[n_umask], vecs[n_umask]))][0]
            rotation[2] = np.array(c)
            rotation[0] = np.cross(rotation[1], rotation[2])

        # IEEE rules: c || x3
        elif xtal_sys == "triclinic":
            rotation = [vec / mag for (mag, vec) in sorted(zip(lengths, vecs))]
            rotation = np.roll(rotation, 2, axis=0)
            rotation[1] = get_uvec(np.cross(rotation[2], rotation[1]))
            rotation[0] = np.cross(rotation[1], rotation[2])

        return self.rotate(rotation, tol=1e-2)


class TensorCollection(collections.Sequence):
    """
    A sequence of tensors that can be used for fitting data
    or for having a tensor expansion
    """
    def __init__(self, tensor_list, base_class=Tensor):
        self.tensors = [base_class(t) if not isinstance(t, base_class)
                        else t for t in tensor_list]

    def __len__(self):
        return len(self.tensors)

    def __getitem__(self, ind):
        return self.tensors[ind]

    def __iter__(self):
        return self.tensors.__iter__()

    def zeroed(self, tol=1e-3):
        return self.__class__([t.zeroed(tol) for t in self])

    def transform(self, symm_op):
        return self.__class__([t.transform(symm_op) for t in self])

    def rotate(self, matrix, tol=1e-3):
        return self.__class__([t.rotate(matrix, tol) for t in self])

    @property
    def symmetrized(self):
        return self.__class__([t.symmetrized for t in self])

    def is_symmetric(self, tol=1e-5):
        return all([t.is_symmetric(tol) for t in self])

    def fit_to_structure(self, structure, symprec=0.1):
        return self.__class__([t.fit_to_structure(structure, symprec) 
                               for t in self])

    @property
    def voigt(self):
        return [t.voigt for t in self]

    def is_voigt_symmetric(self, tol=1e-6):
        return all([t.is_voigt_symmetric(tol) for t in self])

    @classmethod
    def from_voigt(cls, voigt_input_list, base_class=Tensor):
        return cls([base_class.from_voigt(v) for v in voigt_input_list])

    def convert_to_ieee(self, structure):
        return self.__class__([t.convert_to_ieee(structure) for t in self])


class SquareTensor(Tensor):
    """
    Base class for doing useful general operations on second rank tensors
    (stress, strain etc.).
    """

    def __new__(cls, input_array, vscale=None):
        """
        Create a SquareTensor object.  Note that the constructor uses __new__
        rather than __init__ according to the standard method of
        subclassing numpy ndarrays.  Error is thrown when the class is
        initialized with non-square matrix.

        Args:
            input_array (3x3 array-like): the 3x3 array-like
                representing the content of the tensor
            vscale (6x1 array-like): 6x1 array-like scaling the
                voigt-notation vector with the tensor entries
        """

        obj = super(SquareTensor, cls).__new__(cls, input_array, vscale,
                                               check_rank=2)
        return obj.view(cls)

    @property
    def trans(self):
        """
        shorthand for transpose on SquareTensor
        """
        return SquareTensor(np.transpose(self))

    @property
    def inv(self):
        """
        shorthand for matrix inverse on SquareTensor
        """
        if self.det == 0:
            raise ValueError("SquareTensor is non-invertible")
        return SquareTensor(np.linalg.inv(self))

    @property
    def det(self):
        """
        shorthand for the determinant of the SquareTensor
        """
        return np.linalg.det(self)

    def is_rotation(self, tol=1e-3, include_improper=True):
        """
        Test to see if tensor is a valid rotation matrix, performs a
        test to check whether the inverse is equal to the transpose
        and if the determinant is equal to one within the specified
        tolerance

        Args:
            tol (float): tolerance to both tests of whether the
                the determinant is one and the inverse is equal
                to the transpose
            include_improper (bool): whether to include improper
                rotations in the determination of validity
        """
        det = np.abs(np.linalg.det(self))
        if include_improper:
            det = np.abs(det)
        return (np.abs(self.inv - self.trans) < tol).all() \
            and (np.abs(det - 1.) < tol)

    def get_scaled(self, scale_factor):
        """
        Scales the tensor by a certain multiplicative scale factor

        Args:
            scale_factor (float): scalar multiplier to be applied to the
                SquareTensor object
        """
        return SquareTensor(self * scale_factor)

    @property
    def principal_invariants(self):
        """
        Returns a list of principal invariants for the tensor,
        which are the values of the coefficients of the characteristic
        polynomial for the matrix
        """
        return np.poly(self)[1:] * np.array([-1, 1, -1])

    def polar_decomposition(self, side='right'):
        """
        calculates matrices for polar decomposition
        """
        return polar(self, side=side)


def symmetry_reduce(tensors, structure, tol=1e-8, **kwargs):
    """
    Function that converts a list of tensors corresponding to a structure
    and returns a dictionary consisting of unique tensor keys with symmop
    values corresponding to transformations that will result in derivative
    tensors from the original list

    Args:
        tensors (list of tensors): list of Tensor objects to test for
            symmetrically-equivalent duplicates
        structure (Structure): structure from which to get symmetry
        tol (float): tolerance for tensor equivalence
        kwargs: keyword arguments for the SpacegroupAnalyzer

    returns:
        dictionary consisting of unique tensors with symmetry operations
        corresponding to those which will reconstruct the remaining
        tensors as values
    """
    sga = SpacegroupAnalyzer(structure, **kwargs)
    symmops = sga.get_symmetry_operations(cartesian=True)
    unique_tdict = {}
    for tensor in tensors:
        is_unique = True
        for unique_tensor, symmop in itertools.product(unique_tdict, symmops):
            if (np.abs(unique_tensor.transform(symmop) - tensor) < tol).all():
                unique_tdict[unique_tensor].append(symmop)
                is_unique = False
                break
        if is_unique:
            unique_tdict[tensor] = []
    return unique_tdict
