app_name = "fuelbuddy_creditlimit"
app_title = "Fuelbuddy Creditlimit"
app_publisher = "Fuelbuddy"
app_description = "Credit Limit"
app_email = "shantanu.mishra@fuelbuddy.in"
app_license = "mit"

# Apps
# ------------------

required_apps = ["fuelbuddy_crm", "fuelbuddy_wallet"]

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

# Credit control: money events mark the customer dirty; a minute cron recomputes it.
_CC = "fuelbuddy_creditlimit.credit_control"
doc_events = {
	# Per team convention: one submit-side and one cancel-side handler per doctype.
	"Delivery Note": {
		"on_update": f"{_CC}.mark_on_delivery_note_save",
		"on_cancel": f"{_CC}.mark_on_delivery_note_cancel",
		"after_delete": f"{_CC}.mark_on_delivery_note_cancel",
	},
	"Sales Invoice": {
		"on_submit": f"{_CC}.mark_on_invoice_submit",
		"on_update_after_submit": f"{_CC}.mark_on_invoice_submit",
		"on_cancel": f"{_CC}.mark_on_invoice_cancel",
	},
	"Payment Entry": {
		"on_submit": f"{_CC}.mark_on_payment_entry_submit",
		"on_update_after_submit": f"{_CC}.mark_on_payment_entry_submit",
		"on_cancel": f"{_CC}.mark_on_payment_entry_cancel",
	},
	"Journal Entry": {
		"on_submit": f"{_CC}.mark_on_journal_entry_submit",
		"on_update_after_submit": f"{_CC}.mark_on_journal_entry_submit",
		"on_cancel": f"{_CC}.mark_on_journal_entry_cancel",
	},
	"Customer": {
		"after_insert": f"{_CC}.mark_on_customer_insert",
	},
	"Fuelbuddy Settings": {
		"validate": f"{_CC}.validate_credit_settings",
	},
}

fixtures = [
	{
		"dt": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				[
					"Fuelbuddy Settings-custom_credit_insured_factor",
					"Fuelbuddy Settings-custom_credit_warning_percent",
					"Fuelbuddy Settings-custom_credit_column_break",
					"Fuelbuddy Settings-custom_credit_overdue_grace_days",
					"Fuelbuddy Settings-custom_credit_overdue_min_amount",
					"Fuelbuddy Settings-custom_credit_unbilled_from",
					"Fuelbuddy Settings-custom_credit_exception_max_days",
				],
			]
		],
	},
	{
		"dt": "Property Setter",
		"filters": [
			[
				"name",
				"in",
				[
					"Fuelbuddy Settings-main-track_changes",
					"Fuelbuddy Settings-enable_credit_limit-description",
				],
			]
		],
	},
]

# Scheduled Tasks
# ---------------
# Drain dirty customers every minute; re-mark all every 15 (due dates and exception expiries have no event).
scheduler_events = {
	"cron": {
		"* * * * *": [f"{_CC}.drain_dirty"],
		"*/15 * * * *": [f"{_CC}.sweep_credit"],
	},
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

