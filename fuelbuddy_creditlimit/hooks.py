app_name = "fuelbuddy_creditlimit"
app_title = "Fuelbuddy Creditlimit"
app_publisher = "Fuelbuddy"
app_description = "Credit Limit"
app_email = "shantanu.mishra@fuelbuddy.in"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "fuelbuddy_creditlimit",
# 		"logo": "/assets/fuelbuddy_creditlimit/logo.png",
# 		"title": "Fuelbuddy Creditlimit",
# 		"route": "/fuelbuddy_creditlimit",
# 		"has_permission": "fuelbuddy_creditlimit.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/fuelbuddy_creditlimit/css/fuelbuddy_creditlimit.css"
# app_include_js = "/assets/fuelbuddy_creditlimit/js/fuelbuddy_creditlimit.js"

# include js, css files in header of web template
# web_include_css = "/assets/fuelbuddy_creditlimit/css/fuelbuddy_creditlimit.css"
# web_include_js = "/assets/fuelbuddy_creditlimit/js/fuelbuddy_creditlimit.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "fuelbuddy_creditlimit/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "fuelbuddy_creditlimit/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "fuelbuddy_creditlimit.utils.jinja_methods",
# 	"filters": "fuelbuddy_creditlimit.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "fuelbuddy_creditlimit.install.before_install"
# after_install = "fuelbuddy_creditlimit.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "fuelbuddy_creditlimit.uninstall.before_uninstall"
# after_uninstall = "fuelbuddy_creditlimit.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "fuelbuddy_creditlimit.utils.before_app_install"
# after_app_install = "fuelbuddy_creditlimit.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "fuelbuddy_creditlimit.utils.before_app_uninstall"
# after_app_uninstall = "fuelbuddy_creditlimit.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "fuelbuddy_creditlimit.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# Credit control: enforce credit limit / overdue on Delivery Notes and keep each
# customer's Credit record in step with their transactions. Cash-advance customers
# are skipped inside the handlers (the Wallet app enforces those instead).
_CC = "fuelbuddy_creditlimit.credit_control"
doc_events = {
	# Per team convention: one submit-side and one cancel-side handler per doctype.
	"Delivery Note": {
		"validate": f"{_CC}.check_delivery_note",
		"on_submit": f"{_CC}.update_on_delivery_note_submit",
		"on_cancel": f"{_CC}.update_on_delivery_note_cancel",
	},
	"Sales Invoice": {
		"on_submit": f"{_CC}.refresh_on_invoice_submit",
		"on_cancel": f"{_CC}.refresh_on_invoice_cancel",
	},
	"Payment Entry": {
		"on_submit": f"{_CC}.refresh_on_payment_entry_submit",
		"on_cancel": f"{_CC}.refresh_on_payment_entry_cancel",
	},
	"Finance Dossier": {
		"on_submit": f"{_CC}.sync_limit_from_finance_dossier",
		# Safety net mirroring the dossier's own SO-creation retry: if the customer
		# wasn't resolvable at submit, sync when the submitted dossier is saved again.
		"on_update_after_submit": f"{_CC}.sync_limit_from_finance_dossier",
	},
	"Customer": {
		"after_insert": f"{_CC}.create_credit_for_customer",
	},
}

# Scheduled Tasks
# ---------------
# Recompute every Credit record daily so invoices that cross their due date flip
# has_overdue -- a due date passing has no document event to hook into.
scheduler_events = {
	"daily": [
		"fuelbuddy_creditlimit.credit_control.refresh_overdue_daily",
	],
}

# scheduler_events = {
# 	"all": [
# 		"fuelbuddy_creditlimit.tasks.all"
# 	],
# 	"daily": [
# 		"fuelbuddy_creditlimit.tasks.daily"
# 	],
# 	"hourly": [
# 		"fuelbuddy_creditlimit.tasks.hourly"
# 	],
# 	"weekly": [
# 		"fuelbuddy_creditlimit.tasks.weekly"
# 	],
# 	"monthly": [
# 		"fuelbuddy_creditlimit.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "fuelbuddy_creditlimit.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "fuelbuddy_creditlimit.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "fuelbuddy_creditlimit.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["fuelbuddy_creditlimit.utils.before_request"]
# after_request = ["fuelbuddy_creditlimit.utils.after_request"]

# Job Events
# ----------
# before_job = ["fuelbuddy_creditlimit.utils.before_job"]
# after_job = ["fuelbuddy_creditlimit.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"fuelbuddy_creditlimit.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

