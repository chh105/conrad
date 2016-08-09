from os import path
import json, yaml
from traceback import format_exc

from conrad.compat import *
from conrad.physics.units import percent, Gy, cGy, cm3, Gray, DeliveredDose
from conrad.physics.string import *
from conrad.medicine.structure import Structure
from conrad.medicine.anatomy import Anatomy
from conrad.medicine.dose import D, ConstraintList

"""
TODO: prescription.py docstring

Parsing methods expect following format:

YAML:
=====

- name : PTV
  label : 1
  is_target: Yes
  dose : 35.
  constraints:
  - "D90 >= 32.3Gy"
  - "D1 <= 1.1rx"

- name : OAR1
  label : 2
  is_target: No
  dose :
  constraints:
  - "D95 <= 20Gy"
  - "V30 Gy <= 20%"



Python list of dictionaries (JSON approximately same)
=====================================================

	[{
		'name' : 'PTV',
		'label' : 1,
		'is_target' : True,
		'dose' : 35.,
		'constraints' : ['D1 <= 1.1rx', 'D90 >= 32.3Gy']
	},

	{
 		'name' : 'OAR1',
	  	'label' : 2,
	  	'is_target' : False,
	  	'dose' : None,
	  	'constraints' : ['D95 <= 20Gy']
	}]


( JSON differences:
	- double quote instead of single
	- true/false instead of True/False
	- null instead of None )
"""

def v_strip(input_string):
	return input_string.replace('to', '').replace('V', '').replace('v', '')

def d_strip(input_string):
	return input_string.replace('D', '').replace('d', '')

def string2constraint(string_constraint, rx_dose=None):
	""" convert input string to standard form dose constraint:

		D{p} <= {d} Gy (dose @ pth percentile <= d Gy)

	or

		D{p} >= {d} Gy (dose @ pth percentile >= d Gy)


	input cases:

	absolute dose constraints
	-------------------------

	- "min > x Gy"

		variants: "Min", "min"
		meaning: minimum dose greater than x Gy


	- "mean < x Gy"
	- "mean > x Gy"

		variants: "Mean, mean"
		meaning: mean dose less than (more than) than x Gy


	- "max < x Gy"

		variants: "Max", "max"
		meaning: maximum dose less than x Gy


	- "D__ < x Gy"
	- "D__ > x Gy"

		variants: "D__%", "d__%", "D__", "d__"
		meaning: dose to __ percent of volume less than (greater than) x Gy


	- "V__ Gy < p %"
	- "V__ Gy > p %"

		variants: "V__", "v__", "__ Gy to", "__ to"
		meaning: no more than (at least) __ Gy to p percent of volume

	relative dose constraints
	-------------------------

	- "V__ %rx < p %"
	- "V__ %rx > p %"

		variants: "V__%", "v__%", "V__", "v__"
		meaning: volume receiving __ percent of rx dose less than
			(greater than) p percent of structure volume


	 - "D__ < {frac} rx"
	 - "D__ > {frac} rx"

		variants: "D__%", "d__%", "D__", "d__"
		meaning: dose to __ percent of volume less than (greater than)
			frac * rx


	absolute volume constraints:
	----------------------------
	- "V_ Gy > x cm3"
	- "V_ Gy < x cm3"
	- "V_ rx > x cm3"
	- "V_ rx < x cm3"

		variants: "cc" vs. "cm3" vs. "cm^3"; "V__ _" vs. "v___ _"
		error: convert to relative volume terms

	"""
	string_constraint = str(string_constraint)

	if not isinstance(rx_dose, (type(None), DeliveredDose)):
		raise TypeError(
				'if provided, argument "rx_dose" must be of type {}, '
				'e.g., {} or {}'
				''.format(DeliveredDose, type(Gy), type(cGy)))

	if volume_unit_from_string(string_constraint) is not None:
		raise ValueError(
				'Detected dose volume constraint with absolute volume '
				'units. Convert to percentage.\n(input = {})'
				''.format(string_constraint))

	leq = '<' in string_constraint
	if leq:
		left, right = string_constraint.replace('=', '').split('<')
	else:
		left, right = string_constraint.replace('=', '').split('>')

	rdose = dose_unit_from_string(right) is not None
	ldose = dose_unit_from_string(left) is not None

	if rdose and ldose:
		raise ValueError(
				'Dose constraint cannot have a dose value on both '
				'sides of inequality.\n(input = {})'
				''.format(string_constraint))

	if rdose:
		tokens = ['mean', 'Mean', 'min', 'Min', 'max', 'Max', 'D', 'd']
		if not any(listmap(lambda t : t in left, tokens)):
			raise ValueError(
					'If dose specified on right side of inequality, '
					'left side must contain one of the following '
					'strings: \n.\ninput={}'
					''.format(tokens, string_constraint))

	relative = not rdose and not ldose
	relative &= rx_dose is not None

	if relative and (rdose or ldose):
		raise ValueError(
				'Dose constraint mixes relative and absolute volume '
				'constraint syntax. \n(input = {})'
				''.format(string_constraint))

	if not (rdose or ldose or relative):
		raise ValueError(
				'Dose constraint dose not specify a dose level in Gy '
				'or cGy, and no prescription\ndose was provided '
				'(argument "rx_dose") for parsing a relative dose '
				'constraint. \n(input = {})'
				''.format(string_constraint))

	try:
		# cases:
		# - "min > x Gy"
		# - "mean < x Gy"
		# - "max < x Gy"
		# - "D__% < x Gy"
		# - "D__% > x Gy"
		if rdose:
			#-----------------------------------------------------#
			# constraint in form "{LHS} <> {x} Gy"
			#
			# conversion to canonical form:
			#  (none required)
			#-----------------------------------------------------#

			# parse dose
			dose = dose_from_string(right)

			# parse threshold (min, mean, max or percentile)
			if 'mean' in left or 'Mean' in left:
				threshold = 'mean'
			elif 'min' in left or 'Min' in left:
				threshold = 'min'
			elif 'max' in left or 'Max' in left:
				threshold = 'max'
			else:
				threshold = percent_from_string(d_strip(left))

		# cases:
		# - "V__ Gy < p %" ( == "x Gy to < p %")
		# - "V__ Gy > p %" ( == "x Gy to > p %")
		elif ldose:
			#-----------------------------------------------------#
			# constraint in form "V{x} Gy <> {p} %"
			#
			# conversion to canonical form:
			# {x} Gy < {p} % ---> D{100 - p} < {x} Gy
			# {x} Gy > {p} % ---> D{p} > {x} Gy
			#-----------------------------------------------------#

			# parse dose
			dose = dose_from_string(v_strip(left))

			# parse percentile
			threshold = percent_from_string(right)

			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# VOLUME AT X GY < P % of STRUCTURE
			#
			# 	~equals~
			#
			# X Gy to < P% of structure
			#
			# 	~equivalent to~
			#
			# D(100 - P) < X Gy
			# <<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>
			# VOLUME AT X GY > P% of STRUCTURE
			#
			# 	~equals~
			#
			# X Gy to > P% of structure
			#
			# 	~equivalent to~
			#
			# D(P) > X Gy
			# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
			if leq:
				threshold.value = 100 - threshold.value


		# cases:
		# - "V__% < p %"
		# - "V__% > p %"
		# - "D__% < {frac} rx"
		# - "D__% > {frac} rx"
		else:
			#-----------------------------------------------------#
			# constraint in form "V__% <> p%"
			#
			# conversion to canonical form:
			# V{x}% < {p} % ---> D{100 - p} < {x/100} * {rx_dose} Gy
			# V{x}% > {p} % ---> D{p} > {x/100} * {rx_dose} Gy
			#-----------------------------------------------------#
			if not 'rx' in right:
				# parse dose
				reldose = fraction_or_percent_from_string(
						v_strip(left.replace('rx', '')))
				dose = reldose * rx_dose

				# parse percentile
				threshold = percent_from_string(right)

				if leq:
					threshold.value = 100 - threshold.value

			#-----------------------------------------------------#
			# constraint in form "D{p}% <> {frac} rx" OR
			#					 "D{p}% <> {100 * frac}% rx"
			#
			# conversion to canonical form:
			# D{p}% < {frac} rx ---> D{p} < {frac} * {rx_dose} Gy
			# D{p}% >{frac} rx ---> D{p} > {frac} * {rx_dose} Gy
			#-----------------------------------------------------#
			else:
				# parse dose
				dose = fraction_or_percent_from_string(
						right.replace('rx', '')) * rx_dose

				# parse percentile
				threshold = percent_from_string(d_strip(left))

		if leq:
			return D(threshold) <= dose
		else:
			return D(threshold) >= dose

	except:
		print(str(
				'Unknown parsing error. Input = {}'.format(string_constraint)))
		raise

class Prescription(object):
	""" TODO: docstring """

	def __init__(self, prescription_data=None):
		""" TODO: docstring """
		self.constraint_dict = {}
		self.structure_dict = {}
		self.rx_list = []
		if isinstance(prescription_data, Prescription):
			self.constraint_dict = prescription_data.constraint_dict
			self.structure_dict = prescription_data.structure_dict
			self.rx_list = prescription_data.rx_list
		elif prescription_data:
			self.digest(prescription_data)

	def add_structure_to_dictionaries(self, structure):
		if not isinstance(structure, Structure):
			raise TypeError('argumet "Structure" must be of type {}'
							''.format(Structure))
		self.structure_dict[structure.label] = structure
		self.constraint_dict[structure.label] = ConstraintList()

	def digest(self, prescription_data):
		""" TODO: docstring """

		err = None
		data_valid = False
		rx_list = []

		# read prescription data from list
		if isinstance(prescription_data, list):
			rx_list = prescription_data
			data_valid = True

		# read presription data from file
		if isinstance(prescription_data, str):
			if path.exists(prescription_data):
				try:
					f = open(prescription_data)
					if '.json' in prescription_data:
						rx_list = json.load(f)
					else:
						rx_list = yaml.safe_load(f)
					f.close
					data_valid = True
				except:
					err = format_exc()

		if not data_valid:
			if err is not None:
				print(err)
			raise TypeError(
					'input prescription_data expected to be a list or '
					'the path to a valid JSON or YAML file.')

		try:
			for item in rx_list:
				rx_dose = None
				label = item['label']
				name = item['name']
				dose = 0 * Gy
				is_target = bool(item['is_target'])
				if is_target:
					if isinstance(item['dose'], (float, int)):
						rx_dose = dose = float(item['dose']) * Gy
					else:
						rx_dose = dose = dose_from_string(item['dose'])

				s = Structure(label, name, is_target, dose=dose)
				self.add_structure_to_dictionaries(s)

				if 'constraints' in item:
					if item['constraints'] is not None:
						for string in item['constraints']:
							self.constraint_dict[label] += string2constraint(
									string, rx_dose=rx_dose)
			self.rx_list = rx_list

		except:
			print(str('Unknown error: prescription_data could not be '
					  'converted to conrad.Prescription() datatype.'))
			raise

	@property
	def list(self):
		""" TODO: docstring """
		return self.rx_list

	@property
	def dict(self):
		""" TODO: docstring """
		rx_dict = {}
		for structure in self.rx_list:
			rx_dict[structure.label] = structure
		return self.rx_dict

	@property
	def constraints_by_label(self):
		""" TODO: docstring """
		return self.constraint_dict

	def __str__(self):
		""" TODO: docstring """
		return str(self.rx_list)

	def report(self, anatomy):
		"""TODO: docstring"""
		if not isinstance(anatomy, Anatomy):
			raise TypeError('argument "anatomy" must be of type{}'.format(
							Anatomy))

		rx_constraints = self.constraints_by_label
		report = {}
		for label, s in anatomy.structures.items():
			sat = []
			for constr in rx_constraints[label].itervalues():
				status, dose_achieved = s.satisfies(constr)
				sat.append({'constraint': constr, 'status': status,
							'dose_achieved': dose_achieved})
			report[label] = sat
		return report

	def report_string(self, anatomy):
		report = self.report(anatomy)
		out = ''
		for label, replist in report.items():
			sname = structures[label].name
			sname = '' if sname is None else ' ({})\n'.format(sname)
			for item in replist:
				out += str(
						'{}\tachieved? {}\tdose at level: {}\n'.format(
						 str(item['constraint']), item['status'],
						 item['dose_achieved']))
		return out
