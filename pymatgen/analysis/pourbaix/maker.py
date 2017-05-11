# coding: utf-8
# Copyright (c) Pymatgen Development Team.
# Distributed under the terms of the MIT License.

from __future__ import division, unicode_literals

import logging
import numpy as np
import itertools
from itertools import chain
from scipy.spatial import ConvexHull
from pymatgen.analysis.pourbaix.entry import MultiEntry, ion_or_solid_comp_object
from pymatgen.core.periodic_table import Element
from pymatgen.core.composition import Composition

"""
Module containing analysis classes which compute a pourbaix diagram given a
target compound/element.
"""

from six.moves import zip

__author__ = "Sai Jayaraman"
__copyright__ = "Copyright 2012, The Materials Project"
__version__ = "0.0"
__maintainer__ = "Sai Jayaraman"
__email__ = "sjayaram@mit.edu"
__status__ = "Development"
__date__ = "Nov 1, 2012"


logger = logging.getLogger(__name__)

PREFAC = 0.0591
MU_H2O = -2.4583


class PourbaixDiagram(object):
    """
    Class to create a Pourbaix diagram from entries
    Args:
        entries: Entries list containing both Solids and Ions
        comp_dict: Dictionary of compositions
    """
    def __init__(self, entries, comp_dict=None):
        self._solid_entries = list()
        self._ion_entries = list()
        for entry in entries:
            if entry.phase_type == "Solid":
                self._solid_entries.append(entry)
            elif entry.phase_type == "Ion":
                self._ion_entries.append(entry)
            else:
                raise Exception("Incorrect Phase type - needs to be \
                Pourbaix entry of phase type Ion/Solid")
        if len(self._ion_entries) == 0:
            raise Exception("No ion phase. Equilibrium between ion/solid "
                            "is required to make a Pourbaix Diagram")
        self._unprocessed_entries = self._solid_entries + self._ion_entries
        self._elt_comp = comp_dict
        if comp_dict:
            self._multielement = True
            pbx_elements = set()
            for comp in comp_dict.keys():
                for el in [el for el in
                           ion_or_solid_comp_object(comp).elements
                           if el not in ["H", "O"]]:
                    pbx_elements.add(el.symbol)
            self.pourbaix_elements = pbx_elements
            w = [comp_dict[key] for key in comp_dict]
            A = []
            for comp in comp_dict:
                comp_obj = ion_or_solid_comp_object(comp)
                Ai = []
                for elt in self.pourbaix_elements:
                    Ai.append(comp_obj[elt])
                A.append(Ai)
            A = np.array(A).T.astype(float)
            w = np.array(w)
            A /= np.dot([a.sum() for a in A], w)
            x = np.linalg.solve(A, w)
            self._elt_comp = dict(zip(self.pourbaix_elements, x))

        else:
            self._multielement = False
            self.pourbaix_elements = [el.symbol
                                      for el in entries[0].composition.elements
                                      if el.symbol not in ["H", "O"]]
            self._elt_comp = {self.pourbaix_elements[0]: 1.0}
        self._make_pourbaixdiagram()

    def _create_conv_hull_data(self):
        """
        Make data conducive to convex hull generator.
        """
        if self._multielement:
            self._all_entries = self._process_multielement_entries()
        else:
            self._all_entries = self._unprocessed_entries
        entries_to_process = list()
        for entry in self._all_entries:
            entry.scale(entry.normalization_factor)
            entry.correction += (- MU_H2O * entry.nH2O + entry.conc_term)
            entries_to_process.append(entry)
        self._qhull_entries = entries_to_process
        return self._process_conv_hull_data(entries_to_process)

    def _process_conv_hull_data(self, entries_to_process):
        """
        From a sequence of ion+solid entries, generate the necessary data
        for generation of the convex hull.
        """
        data = []
        for entry in entries_to_process:
            row = [entry.npH, entry.nPhi, entry.g0]
            data.append(row)
        temp = sorted(zip(data, self._qhull_entries),
                      key=lambda x: x[0][2])
        [data, self._qhull_entries] = list(zip(*temp))
        return data

    def _process_multielement_entries(self):
        """
        Create entries for multi-element Pourbaix construction
        """
        N = len(self._elt_comp)  # No. of elements
        entries = self._unprocessed_entries
        el_list = self._elt_comp.keys()
        comp_list = [self._elt_comp[el] for el in el_list]
        list_of_entries = list()

        # generate all possible combinations of compounds
        for j in range(1, N + 1):
            list_of_entries += list(itertools.combinations(list(range(len(entries))), j))

        # initialize processed entries
        processed_entries = list()
        
        # for all combinations
        for index, entry_list in enumerate(list_of_entries):
            # Check if all elements in composition list are present in entry_list
            if not (set([Element(el) for el in el_list]).issubset(set(list(chain.from_iterable([entries[i].composition.keys() for i in entry_list]))))):
                continue
            if len(entry_list) == 1:
                # If only one entry in entry_list, then check if the composition matches with the set composition.
                entry = entries[entry_list[0]]
                dict_of_non_oh = dict(zip([key for key in entry.composition.keys() if key.symbol not in ["O", "H"]], [entry.composition[key] for key in [key for key in entry.composition.keys() if key.symbol not in ["O", "H"]]]))
                if Composition(dict(zip(self._elt_comp.keys(), [self._elt_comp[key] / min([self._elt_comp[key] for key in self._elt_comp.keys()]) for key in self._elt_comp.keys()]))).reduced_formula == Composition(dict(zip(dict_of_non_oh.keys(), [dict_of_non_oh[el] / min([dict_of_non_oh[key] for key in dict_of_non_oh.keys()]) for el in dict_of_non_oh.keys()]))).reduced_formula:
                    processed_entries.append(MultiEntry([entry], [1.0]))
                continue

            # matrix (rows for elements / columns for compounds)   
        
            A = [[0.0] * len(entry_list) for _ in range(N)]
            Ab = [[0.0] * (len(entry_list)+1) for _ in range(N)]
            b = [[0.0] for _ in range(N)]

            # get the entries
            multi_entries = [entries[j] for j in entry_list]

            # fill out the matrices
            # for all compounds
            for j in range(len(entry_list)): 
                entry = entries[entry_list[j]]
                comp = entry.composition
                if entry.phase_type == "Solid":
                    red_fac = comp.get_reduced_composition_and_factor()[1]
                else:
                    red_fac = 1.0

                # for all elements
                for i in range(N): 
                    A[i][j] = comp[Element(list(el_list)[i])]/red_fac
                    Ab[i][j]= A[i][j]

            # fill out the b vector and the rest of the augmented matrix
            for i in range(N):
                b[i] = comp_list[i]
                Ab[i][len(entry_list)]=b[i]
                    
            # get the ranks for Rouche-Capelli theorem
            # rank of A cannot exceed the number of compounds
            # rank of Ab (augmented) may exceed the rank of A (results in inconsistant equations)
            rank_A = np.linalg.matrix_rank(np.array(A))
            rank_Ab = np.linalg.matrix_rank(np.array(Ab))

            # if a unique solution exists 
            if(rank_A == rank_Ab):
                # if the number of compounds is less than the number of elements
                # inevitably have linearly dependent rows
                # A is a nonsquare matrix (number of compounds < number of elements)
                # pick the independent rows and construct a smaller square matrix
                if(len(entry_list) < N):
                    # figure out the linearly dependent rows
                    N_list = list(itertools.combinations(list(range(N)), len(entry_list)))

                    # initialize matrices
                    reduced_A = [[0.0] * len(entry_list) for _ in range(len(entry_list))]
                    reduced_b = [0.0] * len(entry_list)
                
                    # find the reduced matrix with rank equal to the number of compounds
                    found_reduced_A = False
                    for k in range(len(N_list)):
                        row_set = N_list[k]
                        for l in range(len(row_set)):
                            reduced_A[l] = A[row_set[l]]
                            reduced_b[l] = b[row_set[l]]

                        # if the right matrix is found
                        if(np.linalg.matrix_rank(reduced_A)==len(entry_list)):
                            found_reduced_A = True
                            break
                    # if found a non-singular reduced matrix
                    if(found_reduced_A):
                        # try to solve for the weights
                        try:
                            weights = np.linalg.solve(reduced_A,reduced_b)
                        except np.linalg.linalg.LinAlgError as err:
                            # there cannot be any singular matrix
                            # since the rank is equal to the number of compounds
                            if 'Singular matrix' in err.message:
                                continue
                            else:
                                raise Exception("Unknown Error message!")
                        if not(np.all(weights > 0.0)):
                            continue

						# Remove multi-entries where weights a numerically
						# insignificant
                        ignore = False
                        for i in range(len(entry_list)):
                            if multi_entries[i].phase_type == "Solid" and weights[i] < 1e-3:
                                ignore = True
                                continue
                            elif multi_entries[i].phase_type == "Ion" and weights[i] < multi_entries[i].conc:
                                ignore = True
                                continue
                        if ignore:
                             continue

                        weights = list(weights)
                        weight0 = weights[0]
                        for k in range(len(weights)):
                            weights[k] /= weight0
                        super_entry = MultiEntry(multi_entries, weights)
                        processed_entries.append(super_entry)

                    # if all possible reduced matrices are singular
                    # linearly dependent rows, singular matrix
                    

                # if the number of compounds is equal to the number of elements
                else:
                                     
                    # try to solve for the weights
                    try:
                        weights = np.linalg.solve(A, b)
                    except np.linalg.linalg.LinAlgError as err:
                        if 'Singular matrix' in str(err):
                            continue
                        else:
                            raise Exception("Unknown Error message!")
                    if not(np.all(weights > 0.0)):
                        continue

					# Remove multi-entries where weights a numerically
					# insignificant
                    ignore = False
                    for i in range(len(entry_list)):
                        if multi_entries[i].phase_type == "Solid" and weights[i] < 1e-3:
                            ignore = True
                            continue
                        elif multi_entries[i].phase_type == "Ion" and weights[i] < multi_entries[i].conc:
                            ignore = True
                            continue
                    if ignore:
                          continue

                    weights = list(weights)
                    weight0 = weights[0]
                    for k in range(len(weights)):
                        weights[k] /= weight0
                    super_entry = MultiEntry(multi_entries, weights)
                    processed_entries.append(super_entry)

            # if rank(A) is not equal to rank(Ab) solution does not exists

        return processed_entries

    def _make_pourbaixdiagram(self):
        """
        Calculates entries on the convex hull in the dual space.
        """
        stable_entries = set()
        self._qhull_data = self._create_conv_hull_data()
        dim = len(self._qhull_data[0])
        if len(self._qhull_data) < dim:
            raise Exception("Can only do elements with at-least 3 entries"
                                " for now")
        if len(self._qhull_data) == dim:
            self._facets = [list(range(dim))]
        else:
            facets_hull = np.array(ConvexHull(self._qhull_data).simplices)
            self._facets = np.sort(np.array(facets_hull))
            logger.debug("Final facets are\n{}".format(self._facets))

            logger.debug("Removing vertical facets...")
            vert_facets_removed = list()
            for facet in self._facets:
                facetmatrix = np.zeros((len(facet), len(facet)))
                count = 0
                for vertex in facet:
                    facetmatrix[count] = np.array(self._qhull_data[vertex])
                    facetmatrix[count, dim - 1] = 1
                    count += 1
                if abs(np.linalg.det(facetmatrix)) > 1e-8:
                    vert_facets_removed.append(facet)
                else:
                    logger.debug("Removing vertical facet : {}".format(facet))

            logger.debug("Removing UCH facets by eliminating normal.z >0 ...")

            # Find center of hull
            vertices = set()
            for facet in vert_facets_removed:
                for vertex in facet:
                    vertices.add(vertex)
            c = [0.0, 0.0, 0.0]
            c[0] = np.average([self._qhull_data[vertex][0]
                               for vertex in vertices])
            c[1] = np.average([self._qhull_data[vertex][1]
                               for vertex in vertices])
            c[2] = np.average([self._qhull_data[vertex][2]
                               for vertex in vertices])

            # Shift origin to c
            new_qhull_data = np.array(self._qhull_data)
            for vertex in vertices:
                new_qhull_data[vertex] -= c

            # For each facet, find normal n, find dot product with P, and
            # check if this is -ve
            final_facets = list()
            for facet in vert_facets_removed:
                a = new_qhull_data[facet[1]] - new_qhull_data[facet[0]]
                b = new_qhull_data[facet[2]] - new_qhull_data[facet[0]]
                n = np.cross(a, b)
                val = np.dot(n, new_qhull_data[facet[0]])
                if val < 0:
                    n = -n
                if n[2] <= 0:
                    final_facets.append(facet)
                else:
                    logger.debug("Removing UCH facet : {}".format(facet))
            final_facets = np.array(final_facets)
            self._facets = final_facets

        stable_vertices = set()
        for facet in self._facets:
            for vertex in facet:
                stable_vertices.add(vertex)
                stable_entries.add(self._qhull_entries[vertex])
        self._stable_entries = stable_entries
        self._vertices = stable_vertices

    @property
    def facets(self):
        """
        Facets of the convex hull in the form of  [[1,2,3],[4,5,6]...]
        """
        return self._facets

    @property
    def qhull_data(self):
        """
        Data used in the convex hull operation. This is essentially a matrix of
        composition data and energy per atom values created from qhull_entries.
        """
        return self._qhull_data

    @property
    def qhull_entries(self):
        """
        Return qhull entries
        """
        return self._qhull_entries

    @property
    def stable_entries(self):
        """
        Returns the stable entries in the Pourbaix diagram.
        """
        return list(self._stable_entries)
    
    @property
    def unstable_entries(self):
        """
        Returns all unstable entries in the Pourbaix diagram
        """
        return [e for e in self.qhull_entries if e not in self.stable_entries]

    @property
    def all_entries(self):
        """
        Return all entries
        """
        return self._all_entries

    @property
    def vertices(self):
        """
        Return vertices of the convex hull
        """
        return self._vertices

    @property
    def unprocessed_entries(self):
        """
        Return unprocessed entries
        """
        return self._unprocessed_entries
