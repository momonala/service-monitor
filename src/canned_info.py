from src.services import ServiceStatus, SystemInfo

# Stand-in host vitals for dev mode (off-Pi, where /proc and /sys aren't the Pi's).
canned_system_info = SystemInfo(
    hostname="raspberrypi",
    uptime="6d 14h",
    temperature_c=52.6,
    cpu_percent=12.4,
    load_avg=0.42,
    cpu_count=4,
    memory_used_mb=1840,
    memory_total_mb=3886,
    memory_used_pct=47.3,
    disk_used_pct=38.0,
    disk_used_gb=44.7,
    disk_total_gb=117.6,
)

# Website links with icons (icon mapping centralized here, not in template)
websites = [
    {"name": "cycle-tracker", "url": "https://cycle-tracker.mnalavadi.org"},
    {"name": "task-manager", "url": "https://task-manager.mnalavadi.org"},
    {"name": "energy-monitor", "url": "https://energy-monitor.mnalavadi.org"},
    {"name": "usc-vis", "url": "https://usc-vis.mnalavadi.org"},
    {"name": "incognita", "url": "https://incognita.mnalavadi.org"},
    {"name": "trainspotter", "url": "https://trainspotter.mnalavadi.org"},
    {"name": "inspector-detector", "url": "https://inspectordetector.mnalavadi.org"},
    {"name": "pingpong", "url": "https://pingpong.mnalavadi.org"},
    {"name": "Trace", "url": "https://trace.mnalavadi.org"},
    {"name": "spyglass", "url": "https://spyglass.mnalavadi.org"},
    {"name": "What's On the Menu?", "url": "https://whats-on-the-menu.mnalavadi.org"},
]
websites.sort(key=lambda x: x["name"].lower())

# fmt: off
canned_service_statuses = [
    ServiceStatus(name='projects_atc-tour-extension.service', is_active=True, is_failed=False, uptime='2 days', memory=None, cpu='29.406s', last_error=None, full_status='', project_group='atc-tour-extension', suffix=None, ci_status=None),
    ServiceStatus(name='projects_energy-monitor.service', is_active=True, is_failed=False, uptime='1h 58min', memory=None, cpu='7min 52.884s', last_error="Command '['git', 'add', 'data/energy.db.bk']' returned non-zero exit status 128.", full_status='', project_group='energy-monitor', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_energy-monitor_data-backup-scheduler.service', is_active=True, is_failed=False, uptime='1h 58min', memory=None, cpu='17.103s', last_error=None, full_status='', project_group='energy-monitor', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_energy-monitor_mqtt.service', is_active=True, is_failed=False, uptime='1h 58min', memory=None, cpu='5.185s', last_error=None, full_status='', project_group='energy-monitor', suffix='mqtt', ci_status=None),
    ServiceStatus(name='projects_flight-calendar-updater.service', is_active=True, is_failed=False, uptime='2h 15min', memory=None, cpu='5.597s', last_error=None, full_status='', project_group='flight-calendar-updater', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_incognita_dashboard.service', is_active=True, is_failed=False, uptime='1h 58min', memory=None, cpu='5min 15.362s', last_error=None, full_status='', project_group='incognita', suffix='dashboard', ci_status=None),
    ServiceStatus(name='projects_incognita_data-api.service', is_active=True, is_failed=False, uptime='1h 58min', memory=None, cpu='5.185s', last_error=None, full_status='', project_group='incognita', suffix='data-api', ci_status=None),
    ServiceStatus(name='projects_incognita_data-backup-scheduler.service', is_active=True, is_failed=False, uptime='1h 58min', memory=None, cpu='17.103s', last_error=None, full_status='', project_group='incognita', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_inspector-detector.service', is_active=True, is_failed=False, uptime='1h 30min', memory=None, cpu='26min 58.062s', last_error=None, full_status='', project_group='inspector-detector', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_inspector-detector_site.service', is_active=True, is_failed=False, uptime='1h 30min', memory=None, cpu='2min 40.792s', last_error=None, full_status='', project_group='inspector-detector', suffix='site', ci_status=None),
    ServiceStatus(name='projects_pingpong.service', is_active=True, is_failed=False, uptime='2 days', memory=None, cpu='40min 41.174s', last_error=None, full_status='', project_group='pingpong', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_service-monitor.service', is_active=True, is_failed=False, uptime='4h 23min', memory=None, cpu='2min 12.538s', last_error=None, full_status='', project_group='service-monitor', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_spyglass.service', is_active=True, is_failed=False, uptime='4h 23min', memory=None, cpu='2min 12.538s', last_error=None, full_status='', project_group='spyglass', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_task-manager.service', is_active=True, is_failed=False, uptime='1h 49min', memory=None, cpu='1min 6.171s', last_error=None, full_status='', project_group='task-manager', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_task-manager_data-backup-scheduler.service', is_active=True, is_failed=False, uptime='1h 49min', memory=None, cpu='270ms', last_error=None, full_status='', project_group='task-manager', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_trainspotter.service', is_active=True, is_failed=False, uptime='2 days', memory=None, cpu='51min 48.859s', last_error=None, full_status='', project_group='trainspotter', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_usc-vis.service', is_active=True, is_failed=False, uptime='1h 20min', memory=None, cpu='1min 2.725s', last_error=None, full_status='', project_group='usc-vis', suffix=None, ci_status='success'),
    ServiceStatus(name='projects_usc-vis_data-backup-scheduler.service', is_active=True, is_failed=False, uptime='1h 20min', memory=None, cpu='262ms', last_error=None, full_status='', project_group='usc-vis', suffix='data-backup-scheduler', ci_status=None),
    ServiceStatus(name='projects_wordle-alarm.service', is_active=True, is_failed=False, uptime='6min', memory=None, cpu='2.417s', last_error=None, full_status='', project_group='wordle-alarm', suffix=None, ci_status='success')
]
# fmt: on
