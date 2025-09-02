# Slurm GPU Monitor

A real-time, multi-page GPU cluster monitoring tool for Slurm with a terminal UI similar to htop. Features include live updates, SQLite logging for time series data, queue monitoring, and Discord notifications.

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
[![PyPI](https://img.shields.io/pypi/v/slurm-monitor.svg)](https://pypi.org/project/slurm-monitor/)

## Features

- 🖥️ **Full-screen terminal UI** - Similar to htop, with multiple pages and keyboard navigation
- 📊 **Real-time monitoring** - Live updates of GPU availability, usage, and queue status
- 📈 **Time series logging** - SQLite database logging for historical analysis
- 🚀 **Multi-page interface**:
  - Overview: Quick availability and GPU summary
  - Nodes: Detailed node information
  - Users: User allocations and jobs
  - Queue: Queued jobs aggregated by GPU type and user
  - Summary: Detailed statistics
- 🔔 **Discord notifications** - Optional webhook integration for status updates
- 🎯 **Smart availability calculation** - Excludes drained/down nodes from availability

## Installation

### Using pip

```bash
pip install slurm-monitor
```

### Using uv

```bash
uv pip install slurm-monitor
```

### From source

```bash
git clone https://github.com/AgrawalAmey/slurm-monitor.git
cd slurm-monitor
pip install -e .
```

## Usage

### TUI Version (Recommended) - New!

The TUI version provides a more robust interface using Textual:

```bash
# Start the TUI version
slurm-monitor-tui

# With database logging
slurm-monitor-tui --db

# With custom refresh interval
slurm-monitor-tui --interval 60
```

### Classic Version (Rich-based)

```bash
# Start in live mode (default)
slurm-monitor

# Single run mode
slurm-monitor --no-live

# With custom refresh interval (seconds)
slurm-monitor --interval 60
```

### SQLite Logging

```bash
# Enable database logging
slurm-monitor --db

# Custom database path
slurm-monitor --db --db-path /path/to/metrics.db
```

### Discord Notifications

```bash
# Set webhook URL
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'

# Run with Discord notifications
slurm-monitor --discord --discord-interval 1800
```

### All Options

```bash
slurm-monitor --help

Options:
  --no-live              Disable live monitoring mode (single run)
  --interval SECONDS     Refresh interval in seconds (default: 30)
  --discord              Enable Discord notifications
  --discord-interval SEC Discord notification interval (default: 1800)
  --db                   Enable SQLite logging for time series data
  --db-path PATH         Path to SQLite database (default: gpu_monitor.db)
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1-5` | Switch between pages |
| `Tab` | Next page |
| `←/→` | Previous/Next page |
| `r` | Force refresh |
| `h`, `?` | Show help |
| `q` | Quit |

## Pages

1. **Overview** - Quick GPU availability and summary statistics
2. **Nodes** - Detailed node status with GPU allocation
3. **Users** - User GPU usage and job information
4. **Queue** - Queued jobs aggregated by GPU type and user
5. **Summary** - Detailed statistics with legend

## Database Schema

When using `--db`, the tool creates an SQLite database with four tables:

### gpu_availability
- `timestamp`: Time of measurement
- `gpu_type`: GPU model (a100, h100, etc.)
- `total`, `used`, `available`, `true_available`: GPU counts
- `nodes_total`, `nodes_healthy`: Node counts

### user_usage
- `timestamp`: Time of measurement
- `user`: Username
- `gpu_type`: GPU model
- `gpu_count`: Number of GPUs used
- `job_count`: Number of jobs

### queue_status
- `timestamp`: Time of measurement
- `gpu_type`: GPU model
- `queued_jobs`, `queued_gpus`: Queue metrics
- `unique_users`: Number of unique users in queue

### node_status
- `timestamp`: Time of measurement
- `node_name`: Node identifier
- `state`: Node state (IDLE, ALLOCATED, DRAIN, etc.)
- `gpu_type`: GPU model
- `total_gpus`, `used_gpus`: GPU counts

## Example Queries

```sql
-- GPU utilization over last 7 days
SELECT timestamp, gpu_type, 
       (used * 100.0 / total) as usage_percent
FROM gpu_availability
WHERE timestamp > datetime('now', '-7 days')
ORDER BY timestamp;

-- Top users by GPU hours
SELECT user, gpu_type, 
       SUM(gpu_count * 30) / 3600.0 as gpu_hours
FROM user_usage
WHERE timestamp > datetime('now', '-24 hours')
GROUP BY user, gpu_type
ORDER BY gpu_hours DESC;

-- Queue trends
SELECT DATE(timestamp) as date,
       AVG(queued_jobs) as avg_queued_jobs,
       MAX(queued_gpus) as max_queued_gpus
FROM queue_status
GROUP BY DATE(timestamp)
ORDER BY date DESC;
```

## Requirements

- Python 3.8+
- Slurm commands: `squeue`, `scontrol`
- Terminal with alternate screen buffer support

## Development

```bash
# Clone repository
git clone https://github.com/AgrawalAmey/slurm-monitor.git
cd slurm-monitor

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black slurm_monitor
ruff check slurm_monitor
```

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Author

Amey Agrawal

## Acknowledgments

- Built with [Rich](https://github.com/Textualize/rich) for beautiful terminal UI
- Inspired by htop's interface design