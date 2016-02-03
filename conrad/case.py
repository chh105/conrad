"""
case.py docstring
"""

import os
import sys
import numpy as np
import matplotlib
import cxvpy

class Case(object):
	"""Case object docstring"""
	
	def __init__(self, structures, prescription, num_beams, dvh_constrs_by_struct = None):
		if constraints is None:
			constraints = []
		self.num_beams = num_beams		   # TODO: Extract from A matrix
		self.prescription = prescription
		self.structures = structures
		self.dvh_constrs_by_struct = dvh_constrs_by_struct
	
	def num_beams(self):
		return self.num_beams
	
	def num_dvh_constr(self):
		return sum([dc.count for dc in self.dvh_constrs_by_struct])
	
	def plan(self, wt_under = 1., wt_over = 0.05, wt_oar = 0.2, solver = ECOS, flex_constrs = False, second_pass = False):
		b = self.prescription
		n = self.num_beams()
		n_constr = self.num_dvh_constr()
		
		# Compute weights in objective function
		alpha = wt_over / wt_under
		c_ = (alpha + 1)/2
		d_ = (alpha - 1)/2
		c = c_ * (b > 0) + wt_oar * (b == 0)
		d = d_ * (b > 0)
		
		# Define variables
		x = Variable(n)
		beta = Variable(n_constr)
		
		# Define objective and constraints
		obj = Minimize( c.T * abs(A*x - b) + d.T * (A*x - b) )
		constraints = [x >= 0]
		
		if flex_constrs:
			b_slack = Variable(n_constr)
			obj += Minimize( wt_slack * sum_entries(b_slack) )
			constraints += [b_slack >= 0]
		constraints += self._prob_dvh_constrs(A, b, beta, b_slack, flex_constrs)
		
		prob = Problem(obj, constraints)
		prob.solve(solver = solver)
		if not second_pass:     # TODO: Return beta and b_slack as well?
			return (prob, x)
		
		# Second pass with exact voxel DVH constraints
		x_exact = Variable(n)
		constraints_exact = [x_exact >= 0]
		constraints_exact += self._prob_exact_constrs(A, b, x, x_exact)
		prob_exact = Problem(obj, constr_exact)
		prob_exact.solve(solver = solver)
		return (prob_exact, x_exact)
	
	# Restrict DVH constraints using convex approximation
	def _prob_dvh_constrs(self, A, b, x, b_slack, beta, flex_constrs = False):
		constr_idx = 0
		constr_solver = []
		n_structures = len(self.structures)
		
		for s in xrange(n_structures):
			i_start = self.structures[s].pointer
			i_end = self.structures[s + 1].pointer - 1
			A_sub = A[i_start : i_end, :]
			b_sub = b[i_start : i_end, :]
			
			for dvh_constr in self.dvh_constrs_by_struct[s].constraints:
				p = self.structures[s].size * (dvh_constr.percentile / 100.)
				sign = -1 + 2 * dvh_constr.upper_bound
				b_ = dvh_constr.dose
				if flex_constrs:
					b_ += sign * b_slack[constr_idx]
				
				# Lower bound: \sum max(beta - (Ax - (b - b_slack)), 0) <= beta * p
				# Upper bound: \sum max(beta + (Ax - (b + b_slack)), 0) <= beta * p
				constr = sum_entries(pos(beta[constr_idx] + sign * (A_sub * x - b_) )) <= beta[constr_idx] * p
				constr_solver.append(constr)
				constr_idx += 1
		
		return constr_solver
	
	# Determine exact voxels to constrain
	def _prob_exact_constrs(self, A, b, x, x_exact):
		constr_idx = 0
		constr_exact = []
		n_structures = len(self.structures)
	
		for s in xrange(n_structures):
			i_start = self.structures[s].pointer
			i_end = self.structures[s + 1].pointer - 1
			A_sub = A[i_start : i_end, :]
			b_sub = b[i_start : i_end, :]
			
			for dvh_constr in self.dvh_constrs_by_struct[s].constraints:
				p = self.structures[s].size * (dvh_constr.percentile / 100.)
				sign = -1 + 2 * dvh_constr.upper_bound
				b_ = dvh_constr.dose
				
				# Save p voxels that satisfy constraint by largest margin
				constr_diff = sign * (A_sub.dot(x.value) - b_)
				i_diff = np.argsort(constr_diff, axis = 0)
				i_diff_sub = i_diff[0:floor(p)]
				
				constr = sign * (A_sub[i_diff_sub, :] * x_exact - b_) <= 0
				constr_exact.append(constr)
				constr_idx += 1
		
		return constr_exact
		
