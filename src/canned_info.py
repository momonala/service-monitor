from src.host_info import HostHealth, HostIdentity
from src.services import ServiceState, ServiceStatus, SystemInfo

# Stand-in host vitals for dev mode (off-Pi, where /proc and /sys aren't the Pi's).
canned_system_info = SystemInfo(
    hostname="raspberrypi",
    local_ip="192.168.1.42",
    uptime="6d 14h",
    boot_time="2026-08-17T09:12:00",
    temperature_c=52.6,
    cpu_percent=12.4,
    cpu_count=4,
    memory_used_mb=1840,
    memory_total_mb=3886,
    memory_used_pct=47.3,
    disk_used_pct=38.0,
    disk_used_gb=44.7,
    disk_total_gb=117.6,
)

canned_host_identity = HostIdentity(
    model="Raspberry Pi 4 Model B Rev 1.4",
    os_name="Debian GNU/Linux 12 (bookworm)",
    kernel="6.12.87+rpt-rpi-v8",
    arch="aarch64",
    python_version="3.14.2",
    system_python_version="3.11.2",
)

canned_host_health = HostHealth(
    throttled_now=[],
    throttled_since_boot=["Under-voltage"],
    reboot_required=False,
    apt_updates=3,
    ntp_synced=True,
    tunnel_active=True,
)

canned_websites = [
    {
        "name": "claude-usage-notch-server",
        "project": "claude-usage-notch-server",
        "url": "https://claude-usage-notch-server.mnalavadi.org",
    },
    {"name": "cycle-tracker", "project": "cycle-tracker", "url": "https://cycle-tracker.mnalavadi.org"},
    {"name": "energy-monitor", "project": "energy-monitor", "url": "https://energy-monitor.mnalavadi.org"},
    {"name": "incognita", "project": "incognita", "url": "https://incognita.mnalavadi.org"},
    {
        "name": "inspectordetector",
        "project": "inspector-detector",
        "url": "https://inspectordetector.mnalavadi.org",
    },
    {"name": "my-cloud", "project": "my-cloud", "url": "https://my-cloud.mnalavadi.org"},
    {
        "name": "personal-finances",
        "project": "personal-finances",
        "url": "https://personal-finances.mnalavadi.org",
    },
    {"name": "pingpong", "project": "pingpong", "url": "https://pingpong.mnalavadi.org"},
    {"name": "spyglass", "project": "spyglass", "url": "https://spyglass.mnalavadi.org"},
    {"name": "surprise", "project": "surprise", "url": "https://surprise.mnalavadi.org"},
    {"name": "task-manager", "project": "task-manager", "url": "https://task-manager.mnalavadi.org"},
    {"name": "trace", "project": "incognita", "url": "https://trace.mnalavadi.org"},
    {"name": "trainspotter", "project": "trainspotter", "url": "https://trainspotter.mnalavadi.org"},
    {"name": "usc-vis", "project": "usc-vis", "url": "https://usc-vis.mnalavadi.org"},
]

# fmt: off
canned_service_statuses = [
    ServiceStatus(name='projects_atc-tour-extension.service', state=ServiceState.ACTIVE, uptime='2 days', memory=None, cpu='29.406s', last_error=None, full_status='', project_group='atc-tour-extension', suffix=None, ci_status=None),
    ServiceStatus(name='projects_energy-monitor.service', state=ServiceState.ACTIVE, uptime='1h 58min', memory=None, cpu='7min 52.884s', last_error="Command '['git', 'add', 'data/energy.db.bk']' returned non-zero exit status 128.", full_status='', project_group='energy-monitor', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_energy-monitor_data-backup-scheduler.service', state=ServiceState.ACTIVE, uptime='1h 58min', memory=None, cpu='17.103s', last_error=None, full_status='', project_group='energy-monitor', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_energy-monitor_mqtt.service', state=ServiceState.ACTIVE, uptime='1h 58min', memory=None, cpu='5.185s', last_error=None, full_status='', project_group='energy-monitor', suffix='mqtt', ci_status=None),
    ServiceStatus(name='projects_my-cloud.service', state=ServiceState.ACTIVE, uptime='1h 0min', memory=None, cpu='5.000s', last_error=None, full_status='', project_group='my-cloud', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_flight-calendar-updater.service', state=ServiceState.ACTIVE, uptime='2h 15min', memory=None, cpu='5.597s', last_error=None, full_status='', project_group='flight-calendar-updater', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_incognita_dashboard.service', state=ServiceState.ACTIVE, uptime='1h 58min', memory=None, cpu='5min 15.362s', last_error=None, full_status='', project_group='incognita', suffix='dashboard', ci_status=None),
    ServiceStatus(name='projects_incognita_data-api.service', state=ServiceState.ACTIVE, uptime='1h 58min', memory=None, cpu='5.185s', last_error=None, full_status='', project_group='incognita', suffix='data-api', ci_status=None),
    ServiceStatus(name='projects_incognita_data-backup-scheduler.service', state=ServiceState.ACTIVE, uptime='1h 58min', memory=None, cpu='17.103s', last_error=None, full_status='', project_group='incognita', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_inspector-detector.service', state=ServiceState.ACTIVE, uptime='1h 30min', memory=None, cpu='26min 58.062s', last_error=None, full_status='', project_group='inspector-detector', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_inspector-detector_site.service', state=ServiceState.ACTIVE, uptime='1h 30min', memory=None, cpu='2min 40.792s', last_error=None, full_status='', project_group='inspector-detector', suffix='site', ci_status=None),
    ServiceStatus(name='projects_pingpong.service', state=ServiceState.ACTIVE, uptime='2 days', memory=None, cpu='40min 41.174s', last_error=None, full_status='', project_group='pingpong', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_service-monitor.service', state=ServiceState.ACTIVE, uptime='4h 23min', memory=None, cpu='2min 12.538s', last_error=None, full_status='', project_group='service-monitor', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_spyglass.service', state=ServiceState.ACTIVE, uptime='4h 23min', memory=None, cpu='2min 12.538s', last_error=None, full_status='', project_group='spyglass', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_task-manager.service', state=ServiceState.ACTIVE, uptime='1h 49min', memory=None, cpu='1min 6.171s', last_error=None, full_status='', project_group='task-manager', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_task-manager_data-backup-scheduler.service', state=ServiceState.ACTIVE, uptime='1h 49min', memory=None, cpu='270ms', last_error=None, full_status='', project_group='task-manager', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_trainspotter.service', state=ServiceState.ACTIVE, uptime='2 days', memory=None, cpu='51min 48.859s', last_error=None, full_status='', project_group='trainspotter', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_usc-vis.service', state=ServiceState.ACTIVE, uptime='1h 20min', memory=None, cpu='1min 2.725s', last_error=None, full_status='', project_group='usc-vis', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_usc-vis_data-backup-scheduler.service', state=ServiceState.ACTIVE, uptime='1h 20min', memory=None, cpu='262ms', last_error=None, full_status='', project_group='usc-vis', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_wordle-alarm.service', state=ServiceState.ACTIVE, uptime='6min', memory=None, cpu='2.417s', last_error=None, full_status='', project_group='wordle-alarm', suffix=None, ci_status='success')
]
# fmt: on
