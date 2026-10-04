"""Deploy-time checks (run by every manage.py command, `migrate` included)."""

from django.core.checks import Error, register


@register()
def poe_package_enforces_the_port_policy(app_configs, **kwargs):
    """This site says which switch ports are boards (settings.
    SNMP_SWITCH_PORT_POLICY, pibfpgas/poe.py), but it is the fpgas-online-poe
    package that must ask. One from before it did would serve /snmp/toggle
    for any port to anyone: refuse to run beside it rather than be open
    without saying so."""
    try:
        from snmp_switch.policy import PORT_POLICY_SETTING  # noqa: F401
    except ImportError:
        return [Error(
            "the installed fpgas-online-poe package does not enforce SNMP_SWITCH_PORT_POLICY: "
            "/snmp/status and /snmp/toggle would accept any switch port from anyone",
            hint="install fpgas-online-poe from a commit that has snmp_switch/policy.py",
            id="pibfpgas.E001",
        )]
    return []
