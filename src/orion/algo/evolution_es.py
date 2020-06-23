# -*- coding: utf-8 -*-
"""
:mod:`orion.algo.evolution_es` --
The Evolved Transformer and large-scale evolution of image classifiers
===========================================================================================

.. module:: evolution_es
    :platform: Unix
    :synopsis: Implement evolution to exploit configurations with fixed resource efficiently

"""
import importlib
import logging

import numpy as np

from orion.algo.hyperband import Bracket, Hyperband

logger = logging.getLogger(__name__)

REGISTRATION_ERROR = """
Bad fidelity level {fidelity}. Should be in {budgets}.
Params: {params}
"""

SPACE_ERROR = """
EvolutionES cannot be used if space does not contain a fidelity dimension.
"""

BUDGET_ERROR = """
Cannot build budgets below max_resources;
(max: {}) - (min: {}) > (num_rungs: {})
"""


def compute_budgets(min_resources, max_resources, reduction_factor, nums_population, pairs):
    """Compute the budgets used for each execution of hyperband"""
    budgets_eves = []
    if reduction_factor == 1:
        for i in range(min_resources, max_resources + 1):
            if i == min_resources:
                budgets_eves.append([(nums_population, i)])
            else:
                budgets_eves[0].append((pairs * 2, i))
    else:
        num_brackets = int(np.log(max_resources) / np.log(reduction_factor))
        budgets = []
        budgets_tab = {}  # just for display consideration
        for bracket_id in range(0, num_brackets + 1):
            bracket_budgets = []
            num_trials = int(np.ceil(int((num_brackets + 1) / (num_brackets - bracket_id + 1)) *
                                     (reduction_factor ** (num_brackets - bracket_id))))

            min_resources = max_resources / reduction_factor ** (num_brackets - bracket_id)
            for i in range(0, num_brackets - bracket_id + 1):
                n_i = int(num_trials / reduction_factor ** i)
                min_i = int(min_resources * reduction_factor ** i)
                bracket_budgets.append((n_i, min_i))

                if budgets_tab.get(i):
                    budgets_tab[i].append((n_i, min_i))
                else:
                    budgets_tab[i] = [(n_i, min_i)]

            budgets.append(bracket_budgets)

        for i in range(len(budgets[0])):
            if i == 0:
                budgets_eves.append([(nums_population, budgets[0][i][1])])
            else:
                budgets_eves[0].append((pairs * 2, budgets[0][i][1]))

    return budgets_eves


class EvolutionES(Hyperband):
    """EvolutionES formulates hyperparameter optimization as an evolution.

    For more information on the algorithm,
    see original paper at
    https://arxiv.org/pdf/1703.01041.pdf and
    https://arxiv.org/pdf/1901.11117.pdf

    Real et al. "Large-Scale Evolution of Image Classifiers"
    So et all. "The Evolved Transformer"

    Parameters
    ----------
    space: `orion.algo.space.Space`
        Optimisation space with priors for each dimension.
    seed: None, int or sequence of int
        Seed for the random number generator used to sample new trials.
        Default: ``None``
    repetitions: int
        Number of execution of Hyperband. Default is numpy.inf which means to
        run Hyperband until no new trials can be suggested.

    """

    def __init__(self, space, seed=None, repetitions=np.inf, nums_population=20, mutate=None):
        super(EvolutionES, self).__init__(space, seed=seed, repetitions=repetitions)

        pair = nums_population // 2
        mutate_ratio = 0.3
        self.volatility = 0.001
        self.nums_population = nums_population
        self.nums_comp_pairs = pair
        self.mutate_ratio = mutate_ratio
        self.mutate_attr = mutate
        self.nums_mutate_gene = int((len(self.space.values()) - 1) * mutate_ratio) if int(
            (len(self.space.values()) - 1) * mutate_ratio) > 0 else 1

        self.hurdles = []

        self.population = {}
        for key in range(len(self.space)):
            if not key == self.fidelity_index:
                self.population[key] = -1 * np.ones(nums_population)

        self.performance = np.inf * np.ones(nums_population)

        self.budgets = compute_budgets(self.min_resources, self.max_resources,
                                       self.reduction_factor, nums_population, pair)

        self.brackets = [
            BracketEVES(self, bracket_budgets, 1, space)
            for bracket_budgets in self.budgets
        ]
        self.seed_rng(seed)

    def _get_bracket(self, point):
        """Get the bracket of a point during observe"""
        return self.brackets[0]


class BracketEVES(Bracket):
    """Bracket of rungs for the algorithm Hyperband.

    Parameters
    ----------
    evolutiones: `evolutiones` algorithm
        The evolutiones algorithm object which this bracket will be part of.
    budgets: list of tuple
        Each tuple gives the (n_trials, resource_budget) for the respective rung.
    repetition_id: int
        The id of hyperband execution this bracket belongs to

    """

    def __init__(self, evolutiones, budgets, repetition_id, space):
        super(BracketEVES, self).__init__(evolutiones, budgets, repetition_id)
        self.eves = self.hyperband
        self.space = space
        self.search_space_remove_fidelity = []

        if evolutiones.mutate_attr:
            self.mutate_attr = evolutiones.mutate_attr
        else:
            self.mutate_attr = {"function": "orion.algo.mutate_functions.default_mutate",
                                "multiply_factor": 3.0, "add_factor": 1}

        function_string = self.mutate_attr["function"]
        mod_name, func_name = function_string.rsplit('.', 1)
        mod = importlib.import_module(mod_name)
        self.mutate_func = getattr(mod, func_name)

        for i in range(len(space.values())):
            if not i == self.eves.fidelity_index:
                self.search_space_remove_fidelity.append(i)

    def get_candidates(self, rung_id):
        """Get a candidate for promotion"""
        if self.has_rung_filled(rung_id + 1):
            return []

        rung = self.rungs[rung_id]['results']

        population_range = (self.eves.nums_population
                            if len(list(rung.values())) > self.eves.nums_population
                            else len(list(rung.values())))

        for i in range(population_range):
            for j in self.search_space_remove_fidelity:
                self.eves.population[j][i] = list(rung.values())[i][1][j]
            self.eves.performance[i] = list(rung.values())[i][0]

        population_index = list(range(self.eves.nums_population))
        red_team = np.random.choice(population_index, self.eves.nums_comp_pairs, replace=False)
        diff_list = list(set(population_index).difference(set(red_team)))
        blue_team = np.random.choice(diff_list, self.eves.nums_comp_pairs, replace=False)

        winner_list = []
        loser_list = []

        hurdles = 0
        for i, _ in enumerate(red_team):
            winner = (red_team
                      if self.eves.performance[red_team[i]] < self.eves.performance[blue_team[i]]
                      else blue_team)
            loser = (red_team
                     if self.eves.performance[red_team[i]] >= self.eves.performance[blue_team[i]]
                     else blue_team)

            winner_list.append(winner[i])
            loser_list.append(loser[i])
            hurdles += self.eves.performance[winner[i]]
            self._mutate(winner[i], loser[i])

        hurdles /= len(red_team)
        self.eves.hurdles.append(hurdles)

        logger.debug('Evolution hurdles are: %s', str(self.eves.hurdles))

        points = []
        for i in range(population_range):
            point = [0] * len(self.space)
            nums_all_equal = 0
            while True:
                point[self.eves.fidelity_index] = \
                    list(rung.values())[i][1][self.eves.fidelity_index]

                for j in self.search_space_remove_fidelity:
                    if self.space.values()[j].type == "integer" or \
                       self.space.values()[j].type == "categorical":
                        point[j] = int(self.eves.population[j][i])
                    else:
                        point[j] = self.eves.population[j][i]

                if tuple(point) in points:
                    nums_all_equal += 1
                    logger.debug("find equal, mutate")
                    self._mutate(points.index(tuple(point)), i)
                else:
                    break
                if nums_all_equal > 10:
                    logger.warning("Can not Evolve any more, "
                                   "please stop and use current population.")

            points.append(tuple(point))

        logger.debug('points are: %s', str(points))
        logger.debug('nums points are %d:', len(points))
        return points

    def _mutate(self, winner_id, loser_id):
        self.mutate_func(self, winner_id, loser_id,
                         self.mutate_attr["multiply_factor"],
                         self.mutate_attr["add_factor"])

    def copy_winner(self, winner_id, loser_id):
        """Copy winner to loser"""
        for key in self.search_space_remove_fidelity:
            self.eves.population[key][loser_id] = self.eves.population[key][winner_id].copy()