# coding: utf-8
# Copyright (c) Pymatgen Development Team.
# Distributed under the terms of the MIT License.

from __future__ import division, print_function, unicode_literals, absolute_import

"""
This module implements classes for processing Lammps output files:

    1. log file: contains the thermodynamic data with the format set by the
        'thermo_style' command

    2. trajectory file(dump file): the file generated by the 'dump' command

    Restrictions:
        The first 2 fields of the ATOMS section in the trajectory(dump) file
        must be the atom id and the atom type. There can be arbitrary number
        of fields after that and they all will be treated as floats and
        updated based on the field names in the ITEM: ATOMS line.
"""

import re
import os
from io import open

import numpy as np

from monty.json import MSONable

from pymatgen.core.periodic_table import _pt_data
from pymatgen.core.structure import Structure
from pymatgen.core.lattice import Lattice
from pymatgen.analysis.diffusion_analyzer import DiffusionAnalyzer
from pymatgen.io.lammps.data import LammpsData

__author__ = "Kiran Mathew"
__email__ = "kmathew@lbl.gov"
__credits__ = "Navnidhi Rajput, Michael Humbert"


# TODO write parser for one and multi thermo_styles
class LammpsLog(MSONable):
    """
    Parser for LAMMPS log file.
    """

    def __init__(self, log_file="log.lammps"):
        """
        Args:
            log_file (string): path to the log file
        """
        self.log_file = os.path.abspath(log_file)
        self.timestep = -1
        self._parse_log()

    def _parse_log(self):
        """
        Parse the log file for run and thermodynamic data.
        Sets the thermodynamic data as a structured numpy array with field names
        taken from the custom thermo_style command. thermo_style one and multi
        are not supported yet
        """

        thermo_data = []
        fixes = []
        d_build = None
        thermo_pattern = None
        with open(self.log_file, 'r') as logfile:
            for line in logfile:
                # timestep, the unit depedns on the 'units' command
                time = re.search(r'timestep\s+([0-9]+)', line)
                if time and not d_build:
                    self.timestep = float(time.group(1))
                # total number md steps
                steps = re.search(r'run\s+([0-9]+)', line)
                if steps and not d_build:
                    self.nmdsteps = int(steps.group(1))
                # simulation info
                fix = re.search(r'fix.+', line)
                if fix and not d_build:
                    fixes.append(fix.group())
                # dangerous builds
                danger = re.search(r'Dangerous builds\s+([0-9]+)', line)
                if danger and not d_build:
                    d_build = int(steps.group(1))
                # logging interval
                thermo = re.search(r'thermo\s+([0-9]+)', line)
                if thermo and not d_build:
                    self.interval = float(thermo.group(1))
                # thermodynamic data, set by the thermo_style command
                fmt = re.search(r'thermo_style.+', line)
                if fmt and not d_build:
                    thermo_type = fmt.group().split()[1]
                    fields = fmt.group().split()[2:]
                    no_parse = ["one", "multi"]
                    if thermo_type in no_parse:
                        thermo_data.append("cannot parse thermo_style")
                    else:
                        thermo_pattern_string = r"\s*([0-9eE\.+-]+)" + "".join(
                            [r"\s+([0-9eE\.+-]+)" for _ in range(len(fields) - 1)])
                        thermo_pattern = re.compile(thermo_pattern_string)
                if thermo_pattern:
                    if thermo_pattern.search(line):
                        m = thermo_pattern.search(line)
                        thermo_data.append(tuple([float(x) for x in m.groups()]))

        if thermo_data:
            if isinstance(thermo_data[0], str):
                self.thermo_data = [thermo_data]
            else:
                # numpy arrays are easier to reshape, previously we used np.array with dtypes
                self.thermo_data = {
                    fields[i]: [thermo_data[j][i] for j in range(len(thermo_data))]
                    for i in range(len(fields))}

        self.fixes = fixes
        self.dangerous_builds = d_build

    def as_dict(self):
        d = {}
        for attrib in [a for a in dir(self)
                       if not a.startswith('__') and not callable(getattr(self, a))]:
            d[attrib] = getattr(self, attrib)
        d["@module"] = self.__class__.__module__
        d["@class"] = self.__class__.__name__
        return d

    # not really needed ?
    @classmethod
    def from_dict(cls, d):
        return cls(log_file=d["log_file"])


# TODO: @wood-b parse binary dump files(*.dcd)
class LammpsDump(MSONable):
    """
    Parse lammps dump file.
    """

    def __init__(self, timesteps, natoms, box_bounds, atoms_data):
        self.timesteps = timesteps
        self.natoms = natoms
        self.box_bounds = box_bounds
        self.atoms_data = atoms_data

    @classmethod
    def from_file(cls, dump_file):
        timesteps = []
        atoms_data = []
        natoms = 0
        box_bounds = []
        bb_flag = 0
        parse_timestep, parse_natoms, parse_bb, parse_atoms = False, False, False, False
        with open(dump_file) as tf:
            for line in tf:
                if "ITEM: TIMESTEP" in line:
                    parse_timestep = True
                    continue
                if parse_timestep:
                    timesteps.append(float(line))
                    parse_timestep = False
                if "ITEM: NUMBER OF ATOMS" in line:
                    parse_natoms = True
                    continue
                if parse_natoms:
                    natoms = int(line)
                    parse_natoms = False
                if "ITEM: BOX BOUNDS" in line:
                    parse_bb = True
                    continue
                if parse_bb:
                    box_bounds.append([float(x) for x in line.split()])
                    bb_flag += 1
                    parse_bb = False if bb_flag >= 3 else True
                if "ITEM: ATOMS" in line:
                    parse_atoms = True
                    continue
                if parse_atoms:
                    line_data = [float(x) for x in line.split()]
                    atoms_data.append(line_data)
                    parse_atoms = False if len(atoms_data) == len(timesteps)*natoms else True

        return cls(timesteps, natoms, box_bounds, atoms_data)


# TODO: @wood-b simplify this, use LammpsDump to parse + use mdanalysis to process.
# make sure its backward compatible
class LammpsRun(MSONable):
    """
    Parse the lammps data file, trajectory(dump) file and the log file to extract
    useful info about the system.

    Note: In order to parse trajectory or dump file, the first 2 fields must be
        the id and the atom type. There can be arbitrary number of fields after
        that and they all will be treated as floats.

    Args:
        data_file (str): path to the data file
        trajectory_file (str): path to the trajectory file or dump file
        log_file (str): path to the log file
    """

    def __init__(self, data_file, trajectory_file, log_file="log.lammps"):
        self.data_file = os.path.abspath(data_file)
        self.trajectory_file = os.path.abspath(trajectory_file)
        self.log_file = os.path.abspath(log_file)
        self.log = LammpsLog(log_file)
        self.lammps_data = LammpsData.from_file(self.data_file)
        self._set_mol_masses_and_charges()
        self._parse_trajectory()

    def _parse_trajectory(self):
        """
        parse the trajectory file.
        """
        traj_timesteps = []
        trajectory = []
        timestep_label = "ITEM: TIMESTEP"
        # "ITEM: ATOMS id type ...
        traj_label_pattern = re.compile(
            r"^\s*ITEM:\s+ATOMS\s+id\s+type\s+([A-Za-z0-9[\]_\s]*)")
        # default: id type x y z vx vy vz mol"
        # updated below based on the field names in the ITEM: ATOMS line
        # Note: the first 2 fields must be the id and the atom type. There can
        # be arbitrary number of fields after that and they all will be treated
        # as floats.
        traj_pattern = re.compile(
            r"\s*(\d+)\s+(\d+)\s+([0-9eE.+-]+)\s+([0-9eE.+-]+)\s+"
            r"([0-9eE.+-]+)\s+"
            r"([0-9eE.+-]+)\s+"
            r"([0-9eE.+-]+)\s+([0-9eE.+-]+)\s+(\d+)\s*")
        parse_timestep = False
        with open(self.trajectory_file) as tf:
            for line in tf:
                if timestep_label in line:
                    parse_timestep = True
                    continue
                if parse_timestep:
                    traj_timesteps.append(float(line))
                    parse_timestep = False
                if traj_label_pattern.search(line):
                    fields = traj_label_pattern.search(line).group(1)
                    fields = fields.split()
                    # example:- id type x y z vx vy vz mol ...
                    traj_pattern_string = r"\s*(\d+)\s+(\d+)" + "".join(
                        [r"\s+([0-9eE\.+-]+)" for _ in range(len(fields))])
                    traj_pattern = re.compile(traj_pattern_string)
                if traj_pattern.search(line):
                    # first 2 fields must be id and type, the rest of them
                    # will be casted as floats
                    m = traj_pattern.search(line)
                    line_data = []
                    line_data.append(int(m.group(1)) - 1)
                    line_data.append(int(m.group(2)))
                    line_data.extend(
                        [float(x) for i, x in enumerate(m.groups()) if
                         i + 1 > 2])
                    trajectory.append(tuple(line_data))

        traj_dtype = np.dtype([(str('Atoms_id'), np.int64),
                               (str('atom_type'), np.int64)] +
                              [(str(fld), np.float64) for fld in fields])

        self.trajectory = np.array(trajectory, dtype=traj_dtype)
        self.timesteps = np.array(traj_timesteps, dtype=np.float64)
        for step in range(self.timesteps.size):
            begin = step * self.natoms
            end = (step + 1) * self.natoms
            self.trajectory[begin:end] = np.sort(self.trajectory[begin:end],
                                                 order=str("Atoms_id"))

    def _set_mol_masses_and_charges(self):
        """
        set the charge, mass and the atomic makeup for each molecule
        """
        mol_config = []  # [ [atom id1, atom id2, ...], ... ]
        mol_masses = []  # [ [atom mass1, atom mass2, ...], ... ]
        # mol_charges = []
        unique_atomic_masses = np.array([d["mass"] for d in self.lammps_data.masses])
        mol_ids, atom_ids, atomic_types = [], [], []
        for d in self.lammps_data.atoms:
            mol_ids.append(d["molecule-ID"])
            atom_ids.append(d["id"])
            atomic_types.append(d["type"])
        unique_mol_ids = np.unique(mol_ids)
        atomic_masses = unique_atomic_masses[np.array(atomic_types) - 1]
        self.nmols = unique_mol_ids.size
        for umid in range(self.nmols):
            mol_config.append(np.array(atom_ids)[np.where(mol_ids == umid + 1)] - 1)
            mol_masses.append(atomic_masses[np.where(mol_ids == umid + 1)])
        self.mol_config = np.array(mol_config)
        self.mol_masses = np.array(mol_masses)

    def _weighted_average(self, mol_id, mol_vector):
        """
        Calculate the weighted average of the array comprising of
        atomic vectors corresponding to the molecule with id mol_id.

        Args:
            mol_id (int): molecule id
            mol_vector (numpy array): array of shape,
                natoms_in_molecule with id mol_id x 3

        Returns:
            1D numpy array(3 x 1) of weighted averages in x, y, z directions
        """
        mol_masses = self.mol_masses[mol_id]
        return np.array(
            [np.dot(mol_vector[:, dim], mol_masses) / np.sum(mol_masses)
             for dim in range(3)])

    def _get_mol_vector(self, step, mol_id, param=("x", "y", "z")):
        """
        Returns numpy array corresponding to atomic vectors of parameter
        "param" for the given time step and molecule id

        Args:
            step (int): time step
            mol_id (int): molecule id
            param (list): the atomic parameter for which the weighted
                average is to be computed

        Returns:
            2D numpy array(natoms_in_molecule x 3) of atomic vectors
        """
        begin = step * self.natoms
        end = (step + 1) * self.natoms
        mol_vector_structured = \
            self.trajectory[begin:end][self.mol_config[mol_id]][param]
        new_shape = mol_vector_structured.shape + (-1,)
        mol_vector = mol_vector_structured.view(np.float64).reshape(new_shape)
        return mol_vector.copy()

    # TODO: remove this and use only get_displacements(an order of magnitude faster)
    def get_structures_from_trajectory(self):
        """
        Convert the coordinates in each time step to a structure(boxed molecule).
        Used to construct DiffusionAnalyzer object.

        Returns:
            list of Structure objects
        """
        lattice = Lattice([[self.box_lengths[0], 0, 0],
                           [0, self.box_lengths[1], 0],
                           [0, 0, self.box_lengths[2]]])
        structures = []
        mass_to_symbol = dict(
            (round(y["Atomic mass"], 1), x) for x, y in _pt_data.items())
        unique_atomic_masses = np.array([d["mass"] for d in self.lammps_data.masses])
        for step in range(self.timesteps.size):
            begin = step * self.natoms
            end = (step + 1) * self.natoms
            mol_vector_structured = \
                self.trajectory[begin:end][:][["x", "y", "z"]]
            new_shape = mol_vector_structured.shape + (-1,)
            mol_vector = mol_vector_structured.view(np.float64).reshape(
                new_shape)
            coords = mol_vector.copy()
            species = [mass_to_symbol[round(unique_atomic_masses[atype - 1], 1)]
                       for atype in self.trajectory[begin:end][:]["atom_type"]]
            try:
                structure = Structure(lattice, species, coords,
                                      coords_are_cartesian=True)
            except ValueError as error:
                print("Error: '{}' at timestep {} in the trajectory".format(
                    error,
                    int(self.timesteps[step])))
            structures.append(structure)
        return structures

    def get_displacements(self):
        """
        Return the initial structure and displacements for each time step.
        Used to interface with the DiffusionAnalyzer.

        Returns:
            Structure object, numpy array of displacements
        """
        lattice = Lattice([[self.box_lengths[0], 0, 0],
                           [0, self.box_lengths[1], 0],
                           [0, 0, self.box_lengths[2]]])
        mass_to_symbol = dict(
            (round(y["Atomic mass"], 1), x) for x, y in _pt_data.items())
        unique_atomic_masses = np.array([d["mass"] for d in self.lammps_data.masses])
        frac_coords = []
        for step in range(self.timesteps.size):
            begin = step * self.natoms
            end = (step + 1) * self.natoms
            mol_vector_structured = \
                self.trajectory[begin:end][:][["x", "y", "z"]]
            new_shape = mol_vector_structured.shape + (-1,)
            mol_vector = mol_vector_structured.view(np.float64).reshape(
                new_shape)
            coords = mol_vector.copy()
            if step == 0:
                species = [
                    mass_to_symbol[round(unique_atomic_masses[atype - 1], 1)]
                    for atype in self.trajectory[begin:end][:]["atom_type"]]
                structure = Structure(lattice, species, coords,
                                      coords_are_cartesian=True)
            step_frac_coords = [lattice.get_fractional_coords(crd)
                                for crd in coords]
            frac_coords.append(np.array(step_frac_coords)[:, None])
        frac_coords = np.concatenate(frac_coords, axis=1)
        dp = frac_coords[:, 1:] - frac_coords[:, :-1]
        dp = dp - np.round(dp)
        f_disp = np.cumsum(dp, axis=1)
        disp = lattice.get_cartesian_coords(f_disp)
        return structure, disp

    def get_diffusion_analyzer(self, specie, temperature, time_step, step_skip,
                               **kwargs):
        """
        Args:
            specie (Element/Specie): Specie to calculate diffusivity for as a
                String. E.g., "Li".
            temperature (float): Temperature of the diffusion run in Kelvin.
            time_step (int): Time step between measurements.
            step_skip (int): Sampling frequency of the displacements (
                time_step is multiplied by this number to get the real time
                between measurements)

            For the other parameters please see the
            pymatgen.analysis.diffusion_analyzer.DiffusionAnalyzer documentation.

        Returns:
            DiffusionAnalyzer
        """
        # structures = self.get_structures_from_trajectory()
        structure, disp = self.get_displacements()
        return DiffusionAnalyzer(structure, disp, specie, temperature,
                                 time_step, step_skip=step_skip,
                                 **kwargs)

    @property
    def natoms(self):
        return len(self.lammps_data.atoms)

    @property
    def box_lengths(self):
        return [l[1] - l[0] for l in self.lammps_data.box_bounds]

    @property
    def traj_timesteps(self):
        """
        trajectory time steps in time units.
        e.g. for units = real, time units = fmsec
        """
        return self.timesteps * self.log.timestep

    @property
    def mol_trajectory(self):
        """
        Compute the weighted average trajectory of each molecule at each
        timestep

        Returns:
            2D numpy array ((n_timesteps*mols_number) x 3)
        """
        traj = []
        for step in range(self.timesteps.size):
            tmp_mol = []
            for mol_id in range(self.nmols):
                mol_coords = self._get_mol_vector(step, mol_id,
                                                  param=["x", "y", "z"])
                # take care of periodic boundary conditions
                pbc_wrap(mol_coords, self.box_lengths)
                tmp_mol.append(self._weighted_average(mol_id, mol_coords))
            traj.append(tmp_mol)
        return np.array(traj)

    @property
    def mol_velocity(self):
        """
         Compute the weighted average velcoity of each molecule at each
         timestep.

         Returns:
            2D numpy array ((n_timesteps*mols_number) x 3)
        """
        velocity = []
        for step in range(self.timesteps.size):
            tmp_mol = []
            for mol_id in range(self.nmols):
                mol_velocities = self._get_mol_vector(step, mol_id,
                                                      param=["vx", "vy", "vz"])
                tmp_mol.append(self._weighted_average(mol_id, mol_velocities))
            velocity.append(tmp_mol)
        return np.array(velocity)

    def as_dict(self):
        d = {}
        skip = ["mol_velocity", "mol_trajectory"]  # not applicable in general
        attributes = [a for a in dir(self) if a not in skip and not a.startswith('__')]
        attributes = [a for a in attributes if not callable(getattr(self, a))]
        for attrib in attributes:
            obj = getattr(self, attrib)
            if isinstance(obj, MSONable):
                d[attrib] = obj.as_dict()
            elif isinstance(obj, np.ndarray):
                d[attrib] = obj.tolist()
            else:
                d[attrib] = obj
        d["@module"] = self.__class__.__module__
        d["@class"] = self.__class__.__name__
        return d

    # not really needed ?
    @classmethod
    def from_dict(cls, d):
        return cls(data_file=d["data_file"], trajectory_file=d["trajectory_file"],
                   log_file=d["log_file"])


def pbc_wrap(array, box_lengths):
    """
    wrap the array for molecule coordinates around the periodic boundary.

    Args:
        array (numpy.ndarray): molecule coordinates, [[x1,y1,z1],[x2,y2,z2],..]
        box_lengths (list): [x_length, y_length, z_length]
    """
    ref = array[0, 0]
    for i in range(3):
        array[:, i] = np.where((array[:, i] - ref) >= box_lengths[i] / 2,
                               array[:, i] - box_lengths[i], array[:, i])
        array[:, i] = np.where((array[:, i] - ref) < -box_lengths[i] / 2,
                               array[:, i] + box_lengths[i], array[:, i])
