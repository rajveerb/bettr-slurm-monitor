# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Installation and Setup
```bash
# Development installation
pip install -e ".[dev]"

# Regular installation
pip install git+https://github.com/AgrawalAmey/slurm-monitor.git
```

### Code Quality
```bash
# Format code
black slurm_monitor

# Lint code
ruff check slurm_monitor

# Run tests
pytest
```

### Running the Application
```bash
# Basic usage
slurm-monitor

# With database logging
slurm-monitor --db --db-path gpu_monitor.db

# With custom refresh interval
slurm-monitor --interval 60

# With Discord notifications
slurm-monitor --webhook "https://discord.com/api/webhooks/..."
```

## Architecture Overview

This is a real-time GPU cluster monitoring tool for Slurm-managed HPC environments with a terminal UI built using the Textual framework.

### Core Components

**Main Application (`slurm_monitor/main.py`)**
- `SlurmMonitorApp`: Main Textual TUI application class that orchestrates the entire interface
- `SlurmCommands`: Static utility class for executing Slurm commands (`scontrol`, `squeue`)
- Three main widget classes for different views:
  - `OverviewWidget`: GPU availability summary and heavy users display
  - `NodesWidget`: Detailed per-node status with scrollable tables 
  - `QueueWidget`: Pending job queue analysis with GPU hours calculation

### Key Architecture Patterns

**Data Flow**
1. Background worker threads fetch data from Slurm commands every 30 seconds (configurable)
2. Data is processed and aggregated in worker threads to avoid blocking UI
3. UI updates happen via `call_from_thread()` to ensure thread safety
4. Optional SQLite logging and Discord notifications run in background

**Threading Model**
- Main thread handles Textual UI and user interactions
- Background worker threads (`@work(thread=True)`) handle Slurm command execution
- Database connections are created per-thread to avoid SQLite threading issues

**Widget Architecture**
- Tabbed interface with three main pages (Overview/Nodes/Queue)
- Each widget manages its own loading states and data tables
- Keyboard shortcuts (1-3) for tab switching, r for refresh, q to quit

### Database Schema

When `--db` is enabled, creates four SQLite tables:
- `gpu_availability`: Time-series GPU usage by type
- `user_usage`: User GPU allocation tracking  
- `queue_status`: Pending job queue metrics
- `node_status`: Individual node state history

### External Dependencies

**Required System Commands**
- `scontrol show node -d` - Node information and GPU allocation
- `squeue` - Job queue and allocation data
- Requires access to Slurm cluster environment

**Python Dependencies** 
- `textual>=0.40.0` - Terminal UI framework
- `requests>=2.28.0` - Discord webhook notifications
- Standard library: `sqlite3`, `subprocess`, `asyncio`

### Development Notes

- Uses setuptools build system with pyproject.toml configuration
- Code style: Black formatter with 100 character line length
- Linting: Ruff with Python 3.8+ target
- Entry point: `slurm-monitor` command via `slurm_monitor.main:main`