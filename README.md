# Slurm GPU Monitor

A comprehensive, real-time GPU cluster monitoring tool for Slurm-managed HPC environments with an interactive terminal UI.

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Features

- 🖥️ **Interactive Terminal UI** - Full-screen interface built with Textual framework
- 📊 **Real-time monitoring** - Live updates of GPU availability, usage, and queue status
- 📈 **Time series logging** - SQLite database logging for historical analysis
- 🚀 **Multi-page interface**:
  - Overview: Quick availability, GPU summary, and heavy users
  - Nodes: Detailed node information with scrollable tables
  - Queue: Pending jobs with GPU hours calculation
- 🔔 **Discord notifications** - Optional webhook integration for status updates
- 🎯 **Smart availability calculation** - Excludes drained/down nodes from availability

## Installation

### Via pip/uv

```bash
# Using pip
pip install git+https://github.com/AgrawalAmey/slurm-monitor.git

# Using uv
uv pip install git+https://github.com/AgrawalAmey/slurm-monitor.git
```

### Development Installation

```bash
git clone git@github.com:AgrawalAmey/slurm-monitor.git
cd slurm-monitor
pip install -e .
```

## Usage

```bash
slurm-monitor [options]
```

### Options

```
  --refresh SECONDS      Update interval in seconds (default: 30)
  --webhook URL          Discord webhook URL for notifications
  --db                   Enable SQLite logging for time series data
  --db-path PATH         Path to SQLite database (default: gpu_monitor.db)
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1-3` | Switch between pages (Overview/Nodes/Queue) |
| `↑/↓` | Scroll up/down in tables |
| `PgUp/PgDn` | Page up/down in tables |
| `r` | Force refresh |
| `q` | Quit |

## Pages

1. **Overview** - GPU availability, summary statistics, and top heavy users
2. **Nodes** - Detailed node status with GPU allocation (scrollable)
3. **Queue** - Pending jobs with GPU hours calculation, sorted by resource usage

## Database Schema

When using `--db`, the tool creates an SQLite database with four tables:

### gpu_availability
- `timestamp`: Time of measurement
- `gpu_type`: GPU model (a100, h100, etc.)
- `total`, `used`, `available`: GPU counts
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
- Textual framework for TUI

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

- Built with [Textual](https://github.com/Textualize/textual) for modern terminal UI
- Inspired by htop's interface design