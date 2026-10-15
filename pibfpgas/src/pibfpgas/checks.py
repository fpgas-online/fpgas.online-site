"""Deploy-time checks (run by every manage.py command, `migrate` included)."""

from django.core.checks import Error, register


@register()
def poe_package_enforces_the_port_policy(app_configs, **kwargs):
    """This site says which switch ports may be switched (settings.
    SNMP_SWITCH_PORT_POLICY, pibfpgas/poe.py: every access port of a site
    with a switches file), but it is the fpgas-online-poe package that must
    ask, and that must refuse trunks, uplinks and ports outside the access
    range whatever this site answers. Both arrived in the package together
    with snmp_switch.switches.is_access_port, and only a package with that
    bound has it. Beside an older one /snmp/toggle would reach trunks and
    uplinks: refuse to run rather than be open without saying so."""
    try:
        from snmp_switch.policy import PORT_POLICY_SETTING  # noqa: F401
        from snmp_switch.switches import configured_specs, is_access_port  # noqa: F401  (pibfpgas.poe uses both)
    except ImportError:
        return [Error(
            "the installed fpgas-online-poe package does not limit /snmp/status, /snmp/toggle and "
            "/snmp/power to access ports: it lacks the port policy (SNMP_SWITCH_PORT_POLICY) or the "
            "access-port bound, so they would accept a trunk or an uplink",
            hint="install fpgas-online-poe from a commit that has snmp_switch.switches.is_access_port and "
                 "configured_specs",
            id="pibfpgas.E001",
        )]
    return []
