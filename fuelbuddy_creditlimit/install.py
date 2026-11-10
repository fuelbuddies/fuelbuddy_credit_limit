# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Fresh install: Frappe marks every patch as done without running it (installer.set_all_patches_as_completed),
so create here what the post_model_sync patch creates on an existing site."""

from fuelbuddy_creditlimit.patches import create_credit_exception_workflow


def after_install():
	# The credit roles need no step here: the DocType sync creates every role named in a DocPerm
	# (make_module_and_roles). The Credit Exception approval workflow has no DocType of its own.
	create_credit_exception_workflow.execute()
