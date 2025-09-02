#!/usr/bin/env python3
import subprocess
import re
import sys
import time
import json
import requests
import argparse
import threading
import queue
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box
from rich.live import Live
from rich.columns import Columns
from rich.align import Align
import termios
import tty

console = Console()

# Discord webhook configuration
DISCORD_WEBHOOK_URL = ""  # Set your webhook URL here or via environment variable
import os
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', DISCORD_WEBHOOK_URL)

class DatabaseLogger:
    """Handle SQLite database operations for time series logging"""
    def __init__(self, db_path="gpu_monitor.db"):
        self.db_path = db_path
        self.conn = None
        self.setup_database()
    
    def setup_database(self):
        """Create database tables if they don't exist"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        # GPU availability time series
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gpu_availability (
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                gpu_type TEXT,
                total INTEGER,
                used INTEGER,
                available INTEGER,
                true_available INTEGER,
                nodes_total INTEGER,
                nodes_healthy INTEGER
            )
        ''')
        
        # User usage time series
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_usage (
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user TEXT,
                gpu_type TEXT,
                gpu_count INTEGER,
                job_count INTEGER
            )
        ''')
        
        # Queue status time series
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue_status (
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                gpu_type TEXT,
                queued_jobs INTEGER,
                queued_gpus INTEGER,
                unique_users INTEGER
            )
        ''')
        
        # Node status time series
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS node_status (
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                node_name TEXT,
                state TEXT,
                gpu_type TEXT,
                total_gpus INTEGER,
                used_gpus INTEGER
            )
        ''')
        
        self.conn.commit()
    
    def log_gpu_availability(self, gpu_summary):
        """Log GPU availability data"""
        cursor = self.conn.cursor()
        timestamp = datetime.now()
        
        for gpu_type, info in gpu_summary.items():
            cursor.execute('''
                INSERT INTO gpu_availability 
                (timestamp, gpu_type, total, used, available, true_available, nodes_total, nodes_healthy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                timestamp, gpu_type, 
                info['total'], info['used'], 
                info['total'] - info['used'],
                info['true_available'],
                info['nodes'],
                info['nodes'] - info['drain_nodes']
            ))
        
        self.conn.commit()
    
    def log_user_usage(self, user_gpu_summary):
        """Log user usage data"""
        cursor = self.conn.cursor()
        timestamp = datetime.now()
        
        for user, gpu_types in user_gpu_summary.items():
            for gpu_type, data in gpu_types.items():
                job_count = len(set(job['id'] for job in data['jobs']))
                cursor.execute('''
                    INSERT INTO user_usage 
                    (timestamp, user, gpu_type, gpu_count, job_count)
                    VALUES (?, ?, ?, ?, ?)
                ''', (timestamp, user, gpu_type, data['count'], job_count))
        
        self.conn.commit()
    
    def log_queue_status(self, queue_summary):
        """Log queue status data"""
        cursor = self.conn.cursor()
        timestamp = datetime.now()
        
        for gpu_type, info in queue_summary.items():
            cursor.execute('''
                INSERT INTO queue_status 
                (timestamp, gpu_type, queued_jobs, queued_gpus, unique_users)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                timestamp, gpu_type,
                info['jobs'], info['gpus'], info['users']
            ))
        
        self.conn.commit()
    
    def log_node_status(self, nodes):
        """Log node status data"""
        cursor = self.conn.cursor()
        timestamp = datetime.now()
        
        for node in nodes:
            if 'gpu_type' in node:
                cursor.execute('''
                    INSERT INTO node_status 
                    (timestamp, node_name, state, gpu_type, total_gpus, used_gpus)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp,
                    node['name'],
                    node.get('state', 'UNKNOWN'),
                    node['gpu_type'],
                    node.get('gpu_total', 0),
                    node.get('gpu_used', 0)
                ))
        
        self.conn.commit()
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

class KeyboardListener:
    """Handle keyboard input in a separate thread"""
    def __init__(self):
        self.key_queue = queue.Queue()
        self.running = True
        self.old_settings = None
        
    def start(self):
        """Start listening for keyboard input"""
        self.old_settings = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())
        
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        
    def _listen(self):
        """Listen for keyboard input"""
        while self.running:
            try:
                key = sys.stdin.read(1)
                self.key_queue.put(key)
            except:
                break
                
    def get_key(self):
        """Get a key from the queue if available"""
        try:
            return self.key_queue.get_nowait()
        except queue.Empty:
            return None
            
    def stop(self):
        """Stop listening and restore terminal settings"""
        self.running = False
        if self.old_settings:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)

def get_node_info():
    """Get detailed node information from scontrol"""
    result = subprocess.run(['scontrol', 'show', 'node', '-d'], 
                           capture_output=True, text=True)
    
    nodes = []
    current_node = {}
    
    for line in result.stdout.split('\n'):
        if line.startswith('NodeName='):
            if current_node:
                nodes.append(current_node)
            current_node = {'name': line.split()[0].split('=')[1]}
        elif 'State=' in line:
            match = re.search(r'State=(\S+)', line)
            if match:
                current_node['state'] = match.group(1)
        elif 'Gres=gpu:' in line:
            match = re.search(r'gpu:(\w+):(\d+)', line)
            if match:
                current_node['gpu_type'] = match.group(1)
                current_node['gpu_total'] = int(match.group(2))
        elif 'GresUsed=gpu:' in line:
            match = re.search(r'gpu:\w+:(\d+)', line)
            if match:
                current_node['gpu_used'] = int(match.group(1))
    
    if current_node:
        nodes.append(current_node)
    
    return nodes

def get_job_allocations():
    """Get current job allocations with user information"""
    result = subprocess.run(['squeue', '-o', '%N|%u|%T|%b|%j|%i'], 
                           capture_output=True, text=True)
    
    allocations = defaultdict(lambda: {'users': set(), 'jobs': []})
    
    for line in result.stdout.split('\n')[1:]:  # Skip header
        if not line or '|' not in line:
            continue
        parts = line.split('|')
        if len(parts) >= 5:
            nodelist = parts[0]
            user = parts[1]
            state = parts[2]
            gres = parts[3]
            jobname = parts[4]
            jobid = parts[5] if len(parts) > 5 else 'N/A'
            
            # Only process running jobs with GPU allocations
            if state == 'RUNNING' and 'gpu' in gres:
                # Parse GPU allocation
                gpu_match = re.search(r'gpu:(\w+:)?(\d+)', gres)
                if gpu_match:
                    gpu_count = int(gpu_match.group(2))
                    
                    # Expand nodelist if it's a range
                    nodes = expand_nodelist(nodelist)
                    for node in nodes:
                        allocations[node]['users'].add(user)
                        allocations[node]['jobs'].append({
                            'user': user,
                            'job': jobname,
                            'jobid': jobid,
                            'gpus': gpu_count
                        })
    
    return allocations

def get_queued_jobs():
    """Get queued jobs information"""
    result = subprocess.run(['squeue', '-o', '%u|%T|%b|%j|%i|%Q|%S'], 
                           capture_output=True, text=True)
    
    queued_jobs = []
    
    for line in result.stdout.split('\n')[1:]:  # Skip header
        if not line or '|' not in line:
            continue
        parts = line.split('|')
        if len(parts) >= 5:
            user = parts[0]
            state = parts[1]
            gres = parts[2]
            jobname = parts[3]
            jobid = parts[4]
            priority = parts[5] if len(parts) > 5 else 'N/A'
            start_time = parts[6] if len(parts) > 6 else 'N/A'
            
            # Only process pending jobs with GPU requests
            if state == 'PENDING' and 'gpu' in gres:
                # Parse GPU request
                gpu_match = re.search(r'gpu:(\w+:)?(\d+)', gres)
                if gpu_match:
                    gpu_type = gpu_match.group(1).rstrip(':') if gpu_match.group(1) else 'any'
                    gpu_count = int(gpu_match.group(2))
                    
                    queued_jobs.append({
                        'user': user,
                        'job': jobname,
                        'jobid': jobid,
                        'gpu_type': gpu_type,
                        'gpu_count': gpu_count,
                        'priority': priority,
                        'estimated_start': start_time
                    })
    
    return queued_jobs

def expand_nodelist(nodelist):
    """Expand Slurm nodelist format to individual nodes"""
    try:
        result = subprocess.run(['scontrol', 'show', 'hostname', nodelist],
                               capture_output=True, text=True)
        return result.stdout.strip().split('\n')
    except:
        return [nodelist]

def create_header(current_page, total_pages, last_update):
    """Create the header with navigation info"""
    nav_items = [
        "[1] Overview",
        "[2] Nodes",
        "[3] Users",
        "[4] Queue",
        "[5] Summary"
    ]
    nav_items[current_page] = f"[bold cyan]{nav_items[current_page]}[/bold cyan]"
    nav_text = "  ".join(nav_items)
    
    help_text = "[q] Quit  [h] Help  [r] Refresh"
    
    header_content = f"""[bold cyan]Slurm GPU Monitor[/bold cyan]
{nav_text}
{help_text}
Last Update: {last_update.strftime('%H:%M:%S')}"""
    
    return Panel(header_content, box=box.HEAVY, padding=(0, 2))

def create_overview_page(nodes, allocations):
    """Create the overview page with quick availability and summary"""
    # GPU Summary
    gpu_summary = defaultdict(lambda: {
        'total': 0, 'used': 0, 'nodes': 0, 
        'drain_nodes': 0, 'true_available': 0
    })
    
    for node in nodes:
        if 'gpu_type' in node:
            gpu_type = node['gpu_type']
            total = node.get('gpu_total', 0)
            used = node.get('gpu_used', 0)
            state = node.get('state', '')
            
            gpu_summary[gpu_type]['total'] += total
            gpu_summary[gpu_type]['used'] += used
            gpu_summary[gpu_type]['nodes'] += 1
            
            is_healthy = 'DRAIN' not in state and 'DOWN' not in state
            if not is_healthy:
                gpu_summary[gpu_type]['drain_nodes'] += 1
            else:
                gpu_summary[gpu_type]['true_available'] += (total - used)
    
    summary_table = Table(title="📊 GPU Overview - Quick Availability", box=box.ROUNDED)
    summary_table.add_column("GPU Type", style="cyan", no_wrap=True)
    summary_table.add_column("Total", justify="right", style="white")
    summary_table.add_column("Used", justify="right", style="red")
    summary_table.add_column("Available", justify="right", style="bold green")
    summary_table.add_column("Usage %", justify="right")
    summary_table.add_column("Nodes", justify="right", style="white")
    summary_table.add_column("Healthy", justify="right", style="green")
    
    for gpu_type in sorted(gpu_summary.keys()):
        info = gpu_summary[gpu_type]
        usage_pct = (info['used'] / info['total'] * 100) if info['total'] > 0 else 0
        healthy_nodes = info['nodes'] - info['drain_nodes']
        
        if usage_pct >= 90:
            usage_str = f"[bold red]{usage_pct:.1f}%[/bold red]"
        elif usage_pct >= 70:
            usage_str = f"[yellow]{usage_pct:.1f}%[/yellow]"
        else:
            usage_str = f"[green]{usage_pct:.1f}%[/green]"
        
        # Highlight available GPUs
        avail_str = str(info['true_available'])
        if info['true_available'] > 0:
            avail_str = f"[bold green]{info['true_available']}[/bold green] ✓"
        else:
            avail_str = f"[red]{info['true_available']}[/red]"
        
        summary_table.add_row(
            gpu_type,
            str(info['total']),
            str(info['used']),
            avail_str,
            usage_str,
            str(info['nodes']),
            f"{healthy_nodes}/{info['nodes']}"
        )
    
    # Add totals row
    total_gpus = sum(s['total'] for s in gpu_summary.values())
    total_used = sum(s['used'] for s in gpu_summary.values())
    total_available = sum(s['true_available'] for s in gpu_summary.values())
    total_usage = (total_used / total_gpus * 100) if total_gpus > 0 else 0
    total_nodes = sum(s['nodes'] for s in gpu_summary.values())
    total_healthy = sum(s['nodes'] - s['drain_nodes'] for s in gpu_summary.values())
    
    summary_table.add_section()
    summary_table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total_gpus}[/bold]",
        f"[bold]{total_used}[/bold]",
        f"[bold green]{total_available}[/bold green]" if total_available > 0 else f"[red]{total_available}[/red]",
        f"[bold]{total_usage:.1f}%[/bold]",
        f"[bold]{total_nodes}[/bold]",
        f"[bold]{total_healthy}/{total_nodes}[/bold]"
    )
    
    return Align.center(summary_table, vertical="top")

def create_nodes_page(nodes, allocations):
    """Create the nodes detail page"""
    table = Table(title="🖥️ Node Details", box=box.ROUNDED)
    
    table.add_column("Node", style="cyan", no_wrap=True)
    table.add_column("State", style="yellow")
    table.add_column("GPU Type", style="magenta")
    table.add_column("Total", justify="right")
    table.add_column("Used", justify="right", style="red")
    table.add_column("Available", justify="right", style="green")
    table.add_column("Users", style="blue")
    
    for node in nodes:
        if 'gpu_type' in node:
            total = node.get('gpu_total', 0)
            used = node.get('gpu_used', 0)
            available = total - used
            state = node.get('state', '')
            
            # Style based on state
            if 'DRAIN' in state:
                state_style = f"[red]{state}[/red]"
            elif 'DOWN' in state:
                state_style = f"[bold red]{state}[/bold red]"
            elif 'IDLE' in state:
                state_style = f"[green]{state}[/green]"
            elif 'ALLOCATED' in state or 'MIXED' in state:
                state_style = f"[yellow]{state}[/yellow]"
            else:
                state_style = state
            
            users = ', '.join(sorted(allocations[node['name']]['users'])) if node['name'] in allocations else '-'
            
            table.add_row(
                node['name'],
                state_style,
                node['gpu_type'],
                str(total),
                str(used),
                str(available),
                users if users != '-' else '[dim]-[/dim]'
            )
    
    return Align.center(table, vertical="top")

def create_users_page(allocations, nodes):
    """Create the users page"""
    user_gpu_summary = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'jobs': []}))
    
    for node_name, alloc_info in allocations.items():
        node_info = next((n for n in nodes if n.get('name') == node_name), None)
        if node_info and 'gpu_type' in node_info:
            gpu_type = node_info['gpu_type']
            for job in alloc_info['jobs']:
                user_gpu_summary[job['user']][gpu_type]['count'] += job['gpus']
                user_gpu_summary[job['user']][gpu_type]['jobs'].append({
                    'name': job['job'],
                    'id': job['jobid'],
                    'node': node_name
                })
    
    if not user_gpu_summary:
        return Panel("[dim]No active GPU allocations[/dim]", title="👤 User GPU Usage", box=box.ROUNDED)
    
    table = Table(title="👤 User GPU Usage", box=box.ROUNDED)
    
    table.add_column("User", style="cyan", no_wrap=True)
    table.add_column("GPU Type", style="magenta")
    table.add_column("Count", justify="right", style="yellow")
    table.add_column("Job IDs", style="dim white")
    table.add_column("Job Names", style="blue")
    
    user_totals = {user: sum(gpu_data['count'] for gpu_data in gpus.values()) 
                   for user, gpus in user_gpu_summary.items()}
    
    for user in sorted(user_totals.keys(), key=lambda u: user_totals[u], reverse=True):
        first_row = True
        for gpu_type in sorted(user_gpu_summary[user].keys()):
            gpu_data = user_gpu_summary[user][gpu_type]
            
            unique_jobs = {}
            for job in gpu_data['jobs']:
                if job['id'] not in unique_jobs:
                    unique_jobs[job['id']] = job['name']
            
            job_ids = ', '.join(list(unique_jobs.keys())[:3])
            if len(unique_jobs) > 3:
                job_ids += f" [dim](+{len(unique_jobs)-3})[/dim]"
            
            job_names = ', '.join(list(unique_jobs.values())[:2])
            if len(unique_jobs) > 2:
                job_names += f" [dim](+{len(unique_jobs)-2})[/dim]"
            
            if first_row:
                table.add_row(
                    f"[bold]{user}[/bold]",
                    gpu_type,
                    str(gpu_data['count']),
                    job_ids,
                    job_names
                )
                first_row = False
            else:
                table.add_row(
                    "",
                    gpu_type,
                    str(gpu_data['count']),
                    job_ids,
                    job_names
                )
        
        if user != list(user_totals.keys())[-1]:
            table.add_section()
    
    return Align.center(table, vertical="top")

def create_queue_page(queued_jobs):
    """Create the queue page with aggregated queued jobs"""
    layout = Layout()
    
    # Aggregate by GPU type
    gpu_type_summary = defaultdict(lambda: {'jobs': 0, 'gpus': 0, 'users': set()})
    user_queue_summary = defaultdict(lambda: defaultdict(lambda: {'jobs': 0, 'gpus': 0}))
    
    for job in queued_jobs:
        gpu_type = job['gpu_type']
        user = job['user']
        
        gpu_type_summary[gpu_type]['jobs'] += 1
        gpu_type_summary[gpu_type]['gpus'] += job['gpu_count']
        gpu_type_summary[gpu_type]['users'].add(user)
        
        user_queue_summary[user][gpu_type]['jobs'] += 1
        user_queue_summary[user][gpu_type]['gpus'] += job['gpu_count']
    
    # GPU Type Summary Table
    gpu_table = Table(title="📋 Queue by GPU Type", box=box.ROUNDED)
    gpu_table.add_column("GPU Type", style="cyan")
    gpu_table.add_column("Queued Jobs", justify="right", style="yellow")
    gpu_table.add_column("Total GPUs", justify="right", style="red")
    gpu_table.add_column("Unique Users", justify="right", style="magenta")
    
    for gpu_type in sorted(gpu_type_summary.keys()):
        info = gpu_type_summary[gpu_type]
        gpu_table.add_row(
            gpu_type if gpu_type != 'any' else '[dim]any[/dim]',
            str(info['jobs']),
            str(info['gpus']),
            str(len(info['users']))
        )
    
    # User Queue Summary Table
    user_table = Table(title="👥 Queue by User", box=box.ROUNDED)
    user_table.add_column("User", style="cyan")
    user_table.add_column("GPU Type", style="magenta")
    user_table.add_column("Jobs", justify="right", style="yellow")
    user_table.add_column("GPUs", justify="right", style="red")
    
    # Sort users by total GPUs requested
    user_totals = {user: sum(gpu['gpus'] for gpu in gpus.values()) 
                   for user, gpus in user_queue_summary.items()}
    
    for user in sorted(user_totals.keys(), key=lambda u: user_totals[u], reverse=True)[:10]:  # Top 10 users
        first_row = True
        for gpu_type in sorted(user_queue_summary[user].keys()):
            data = user_queue_summary[user][gpu_type]
            
            if first_row:
                user_table.add_row(
                    f"[bold]{user}[/bold]",
                    gpu_type if gpu_type != 'any' else '[dim]any[/dim]',
                    str(data['jobs']),
                    str(data['gpus'])
                )
                first_row = False
            else:
                user_table.add_row(
                    "",
                    gpu_type if gpu_type != 'any' else '[dim]any[/dim]',
                    str(data['jobs']),
                    str(data['gpus'])
                )
        
        if user != sorted(user_totals.keys(), key=lambda u: user_totals[u], reverse=True)[:10][-1]:
            user_table.add_section()
    
    # Queue Status Panel
    total_jobs = len(queued_jobs)
    total_gpus = sum(job['gpu_count'] for job in queued_jobs)
    total_users = len(set(job['user'] for job in queued_jobs))
    
    status_text = f"""[bold]Queue Status:[/bold]
• Total Jobs: [yellow]{total_jobs}[/yellow]
• Total GPUs Requested: [red]{total_gpus}[/red]
• Unique Users: [magenta]{total_users}[/magenta]"""
    
    status_panel = Panel(status_text, title="📊 Queue Overview", box=box.ROUNDED)
    
    if not queued_jobs:
        return Panel("[green]No jobs in queue![/green]", title="📋 Job Queue", box=box.ROUNDED)
    
    layout.split_column(
        Layout(status_panel, size=7),
        Layout(gpu_table, size=10),
        Layout(user_table)
    )
    
    return layout

def create_summary_page(nodes):
    """Create detailed summary statistics page"""
    gpu_summary = defaultdict(lambda: {
        'total': 0, 'used': 0, 'nodes': 0, 
        'drain_nodes': 0, 'true_available': 0
    })
    
    for node in nodes:
        if 'gpu_type' in node:
            gpu_type = node['gpu_type']
            total = node.get('gpu_total', 0)
            used = node.get('gpu_used', 0)
            state = node.get('state', '')
            
            gpu_summary[gpu_type]['total'] += total
            gpu_summary[gpu_type]['used'] += used
            gpu_summary[gpu_type]['nodes'] += 1
            
            is_healthy = 'DRAIN' not in state and 'DOWN' not in state
            if not is_healthy:
                gpu_summary[gpu_type]['drain_nodes'] += 1
            else:
                gpu_summary[gpu_type]['true_available'] += (total - used)
    
    table = Table(title="📊 Detailed GPU Statistics", box=box.ROUNDED)
    
    table.add_column("GPU Type", style="cyan", no_wrap=True)
    table.add_column("Total GPUs", justify="right", style="white")
    table.add_column("Used", justify="right", style="red")
    table.add_column("Listed Avail", justify="right", style="yellow")
    table.add_column("True Avail", justify="right", style="bold green")
    table.add_column("Usage %", justify="right", style="cyan")
    table.add_column("Nodes", justify="right", style="white")
    table.add_column("Healthy", justify="right", style="green")
    
    for gpu_type in sorted(gpu_summary.keys()):
        info = gpu_summary[gpu_type]
        listed_available = info['total'] - info['used']
        true_available = info['true_available']
        usage_pct = (info['used'] / info['total'] * 100) if info['total'] > 0 else 0
        healthy_nodes = info['nodes'] - info['drain_nodes']
        
        if usage_pct >= 90:
            usage_str = f"[bold red]{usage_pct:.1f}%[/bold red]"
        elif usage_pct >= 70:
            usage_str = f"[yellow]{usage_pct:.1f}%[/yellow]"
        else:
            usage_str = f"[green]{usage_pct:.1f}%[/green]"
        
        table.add_row(
            f"[bold]{gpu_type}[/bold]",
            str(info['total']),
            str(info['used']),
            str(listed_available),
            str(true_available),
            usage_str,
            str(info['nodes']),
            f"{healthy_nodes}/{info['nodes']}"
        )
    
    # Add totals row
    total_gpus = sum(s['total'] for s in gpu_summary.values())
    total_used = sum(s['used'] for s in gpu_summary.values())
    total_listed_available = total_gpus - total_used
    total_true_available = sum(s['true_available'] for s in gpu_summary.values())
    total_usage = (total_used / total_gpus * 100) if total_gpus > 0 else 0
    total_nodes = sum(s['nodes'] for s in gpu_summary.values())
    total_healthy = sum(s['nodes'] - s['drain_nodes'] for s in gpu_summary.values())
    
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total_gpus}[/bold]",
        f"[bold]{total_used}[/bold]",
        f"[bold]{total_listed_available}[/bold]",
        f"[bold green]{total_true_available}[/bold green]",
        f"[bold]{total_usage:.1f}%[/bold]",
        f"[bold]{total_nodes}[/bold]",
        f"[bold]{total_healthy}/{total_nodes}[/bold]"
    )
    
    # Add legend
    legend = Panel(
        "[bold]Legend:[/bold]\n"
        "• Listed Avail: Total GPUs minus used (includes drained nodes)\n"
        "• True Avail: Actually usable GPUs (excludes drained/down nodes)\n"
        "• Healthy: Nodes not in DRAIN or DOWN state",
        box=box.ROUNDED
    )
    
    layout = Layout()
    layout.split_column(
        Layout(table),
        Layout(legend, size=6)
    )
    
    return layout

def create_help_page():
    """Create help page with keyboard shortcuts"""
    help_text = """[bold cyan]Keyboard Shortcuts[/bold cyan]

[bold]Navigation:[/bold]
  1-5     Switch between pages
  Tab     Next page
  ←/→     Previous/Next page

[bold]Actions:[/bold]
  r       Refresh data
  h/?     Show this help
  q       Quit

[bold]Pages:[/bold]
  1. Overview  - Quick availability and summary
  2. Nodes     - Detailed node information
  3. Users     - User allocations and jobs
  4. Queue     - Queued jobs by type and user
  5. Summary   - Detailed statistics

[bold]Information:[/bold]
  • [green]Green[/green] values indicate available resources
  • [red]Red[/red] values indicate used/unavailable resources
  • [yellow]Yellow[/yellow] values indicate partial usage
  • True Avail excludes drained/down nodes

Press any key to return..."""
    
    return Panel(Align.center(help_text, vertical="middle"), 
                 title="📖 Help", box=box.DOUBLE)

def send_discord_notification(gpu_summary, user_gpu_summary):
    """Send GPU availability and usage summary to Discord"""
    if not DISCORD_WEBHOOK_URL:
        return
    
    try:
        # Prepare availability message
        avail_lines = []
        for gpu_type, info in gpu_summary.items():
            if info['true_available'] > 0:
                avail_lines.append(f"• **{gpu_type}**: {info['true_available']} GPUs available")
        
        if not avail_lines:
            avail_msg = "No GPUs currently available"
        else:
            avail_msg = "\n".join(avail_lines)
        
        # Prepare heavy users message
        user_totals = {}
        for user, gpu_types in user_gpu_summary.items():
            for gpu_type, data in gpu_types.items():
                if gpu_type not in user_totals:
                    user_totals[gpu_type] = []
                user_totals[gpu_type].append((user, data['count']))
        
        heavy_users_lines = []
        for gpu_type, users in user_totals.items():
            top_users = sorted(users, key=lambda x: x[1], reverse=True)[:3]
            if top_users:
                users_str = ", ".join([f"{u[0]} ({u[1]})" for u in top_users])
                heavy_users_lines.append(f"• **{gpu_type}**: {users_str}")
        
        heavy_users_msg = "\n".join(heavy_users_lines) if heavy_users_lines else "No active users"
        
        # Create Discord embed
        embed = {
            "title": "🖥️ GPU Cluster Status Update",
            "color": 3447003,  # Blue color
            "timestamp": datetime.utcnow().isoformat(),
            "fields": [
                {
                    "name": "🚀 Available GPUs",
                    "value": avail_msg,
                    "inline": False
                },
                {
                    "name": "👤 Top Users by GPU Type",
                    "value": heavy_users_msg,
                    "inline": False
                }
            ],
            "footer": {
                "text": f"Updated every 30 minutes"
            }
        }
        
        # Send to Discord
        response = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        if response.status_code != 204:
            console.print(f"[yellow]Discord notification failed: {response.status_code}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Discord notification error: {e}[/yellow]")

def main():
    parser = argparse.ArgumentParser(description='GPU Cluster Monitoring Tool')
    parser.add_argument('--no-live', action='store_true', help='Disable live monitoring mode (single run)')
    parser.add_argument('--interval', type=int, default=30, help='Refresh interval in seconds (default: 30)')
    parser.add_argument('--discord', action='store_true', help='Enable Discord notifications')
    parser.add_argument('--discord-interval', type=int, default=1800, help='Discord notification interval in seconds (default: 1800)')
    parser.add_argument('--db', action='store_true', help='Enable SQLite logging for time series data')
    parser.add_argument('--db-path', type=str, default='gpu_monitor.db', help='Path to SQLite database (default: gpu_monitor.db)')
    args = parser.parse_args()
    
    if args.discord and not DISCORD_WEBHOOK_URL:
        console.print("[red]Error: DISCORD_WEBHOOK_URL not set. Set it in the script or as an environment variable.[/red]")
        console.print("Example: export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'")
        return
    
    # Initialize database logger if enabled
    db_logger = DatabaseLogger(args.db_path) if args.db else None
    
    last_discord_time = 0
    
    if not args.no_live:
        # Enter alternate screen buffer for full screen mode
        console.print("\033[?1049h", end="")  # Enter alternate screen
        console.clear()
        
        current_page = 0
        show_help = False
        last_update = datetime.now()
        last_refresh = 0
        
        # Initial data load
        console.print("[bold green]Loading GPU cluster data...[/bold green]")
        nodes = get_node_info()
        allocations = get_job_allocations()
        queued_jobs = get_queued_jobs()
        console.clear()
        
        # Start keyboard listener
        keyboard = KeyboardListener()
        keyboard.start()
        
        try:
            with Live(console=console, refresh_per_second=2, screen=False, auto_refresh=True) as live:
                while True:
                    # Check for keyboard input
                    key = keyboard.get_key()
                    if key:
                        if show_help:
                            show_help = False
                            continue
                            
                        if key in ['q', 'Q']:
                            break
                        elif key in ['h', 'H', '?']:
                            show_help = True
                        elif key == 'r':
                            last_refresh = 0  # Force refresh
                        elif key in ['1', '2', '3', '4', '5']:
                            current_page = int(key) - 1
                        elif key == '\t':  # Tab key
                            current_page = (current_page + 1) % 5
                        elif key == '\x1b':  # Escape sequence for arrow keys
                            next_key = keyboard.get_key()
                            if next_key == '[':
                                arrow = keyboard.get_key()
                                if arrow == 'C':  # Right arrow
                                    current_page = (current_page + 1) % 5
                                elif arrow == 'D':  # Left arrow
                                    current_page = (current_page - 1) % 5
                    
                    # Refresh data if needed
                    current_time = time.time()
                    if current_time - last_refresh >= args.interval:
                        nodes = get_node_info()
                        allocations = get_job_allocations()
                        queued_jobs = get_queued_jobs()
                        last_update = datetime.now()
                        last_refresh = current_time
                        
                        # Log to database if enabled
                        if db_logger:
                            # Create summaries for logging
                            gpu_summary = defaultdict(lambda: {
                                'total': 0, 'used': 0, 'nodes': 0, 
                                'drain_nodes': 0, 'true_available': 0
                            })
                            
                            for node in nodes:
                                if 'gpu_type' in node:
                                    gpu_type = node['gpu_type']
                                    total = node.get('gpu_total', 0)
                                    used = node.get('gpu_used', 0)
                                    state = node.get('state', '')
                                    
                                    gpu_summary[gpu_type]['total'] += total
                                    gpu_summary[gpu_type]['used'] += used
                                    gpu_summary[gpu_type]['nodes'] += 1
                                    
                                    is_healthy = 'DRAIN' not in state and 'DOWN' not in state
                                    if not is_healthy:
                                        gpu_summary[gpu_type]['drain_nodes'] += 1
                                    else:
                                        gpu_summary[gpu_type]['true_available'] += (total - used)
                            
                            user_gpu_summary = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'jobs': []}))
                            for node_name, alloc_info in allocations.items():
                                node_info = next((n for n in nodes if n.get('name') == node_name), None)
                                if node_info and 'gpu_type' in node_info:
                                    gpu_type = node_info['gpu_type']
                                    for job in alloc_info['jobs']:
                                        user_gpu_summary[job['user']][gpu_type]['count'] += job['gpus']
                                        user_gpu_summary[job['user']][gpu_type]['jobs'].append({
                                            'name': job['job'],
                                            'id': job['jobid']
                                        })
                            
                            queue_summary = defaultdict(lambda: {'jobs': 0, 'gpus': 0, 'users': 0})
                            for job in queued_jobs:
                                gpu_type = job['gpu_type']
                                queue_summary[gpu_type]['jobs'] += 1
                                queue_summary[gpu_type]['gpus'] += job['gpu_count']
                            
                            for gpu_type in queue_summary:
                                users = set(job['user'] for job in queued_jobs if job['gpu_type'] == gpu_type)
                                queue_summary[gpu_type]['users'] = len(users)
                            
                            db_logger.log_gpu_availability(dict(gpu_summary))
                            db_logger.log_user_usage(dict(user_gpu_summary))
                            db_logger.log_queue_status(dict(queue_summary))
                            db_logger.log_node_status(nodes)
                        
                        # Send Discord notification if enabled
                        if args.discord and current_time - last_discord_time >= args.discord_interval:
                            # Create summary for Discord
                            gpu_summary = defaultdict(lambda: {'true_available': 0})
                            user_gpu_summary = defaultdict(lambda: defaultdict(lambda: {'count': 0}))
                            
                            for node in nodes:
                                if 'gpu_type' in node:
                                    state = node.get('state', '')
                                    if 'DRAIN' not in state and 'DOWN' not in state:
                                        gpu_type = node['gpu_type']
                                        total = node.get('gpu_total', 0)
                                        used = node.get('gpu_used', 0)
                                        gpu_summary[gpu_type]['true_available'] += (total - used)
                            
                            for node_name, alloc_info in allocations.items():
                                node_info = next((n for n in nodes if n.get('name') == node_name), None)
                                if node_info and 'gpu_type' in node_info:
                                    gpu_type = node_info['gpu_type']
                                    for job in alloc_info['jobs']:
                                        user_gpu_summary[job['user']][gpu_type]['count'] += job['gpus']
                            
                            send_discord_notification(dict(gpu_summary), dict(user_gpu_summary))
                            last_discord_time = current_time
                    
                    # Generate display
                    if show_help:
                        display = create_help_page()
                    else:
                        # Use Group instead of Layout for simpler rendering
                        from rich.console import Group
                        
                        header = create_header(current_page, 5, last_update)
                        
                        if current_page == 0:
                            content = create_overview_page(nodes, allocations)
                        elif current_page == 1:
                            content = create_nodes_page(nodes, allocations)
                        elif current_page == 2:
                            content = create_users_page(allocations, nodes)
                        elif current_page == 3:
                            content = create_queue_page(queued_jobs)
                        else:
                            content = create_summary_page(nodes)
                        
                        display = Group(header, content)
                    
                    live.update(display)
                    time.sleep(0.1)  # Small delay for responsiveness
                    
        except KeyboardInterrupt:
            pass
        finally:
            keyboard.stop()
            if db_logger:
                db_logger.close()
            # Exit alternate screen buffer
            console.print("\033[?1049l", end="")  # Exit alternate screen
            console.print("[yellow]Live monitoring stopped.[/yellow]")
            if args.db:
                console.print(f"[green]Time series data saved to {args.db_path}[/green]")
    else:
        # Single run mode
        console.clear()
        
        with console.status("[bold green]Fetching GPU allocation data...", spinner="dots"):
            nodes = get_node_info()
            allocations = get_job_allocations()
            queued_jobs = get_queued_jobs()
        
        # Show overview page in single run mode
        console.clear()
        header = Panel(
            f"[bold cyan]Slurm GPU Allocation Report[/bold cyan]\n"
            f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
            box=box.DOUBLE,
            padding=(1, 2)
        )
        console.print(header)
        
        # Show all information
        console.print(create_overview_page(nodes, allocations))
        console.print()
        console.print(create_queue_page(queued_jobs))
        console.print()
        console.print(create_nodes_page(nodes, allocations))
        console.print()
        console.print(create_users_page(allocations, nodes))
        
        # Log to database if enabled
        if db_logger:
            # Create summaries for logging
            gpu_summary = defaultdict(lambda: {
                'total': 0, 'used': 0, 'nodes': 0, 
                'drain_nodes': 0, 'true_available': 0
            })
            
            for node in nodes:
                if 'gpu_type' in node:
                    gpu_type = node['gpu_type']
                    total = node.get('gpu_total', 0)
                    used = node.get('gpu_used', 0)
                    state = node.get('state', '')
                    
                    gpu_summary[gpu_type]['total'] += total
                    gpu_summary[gpu_type]['used'] += used
                    gpu_summary[gpu_type]['nodes'] += 1
                    
                    is_healthy = 'DRAIN' not in state and 'DOWN' not in state
                    if not is_healthy:
                        gpu_summary[gpu_type]['drain_nodes'] += 1
                    else:
                        gpu_summary[gpu_type]['true_available'] += (total - used)
            
            user_gpu_summary = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'jobs': []}))
            for node_name, alloc_info in allocations.items():
                node_info = next((n for n in nodes if n.get('name') == node_name), None)
                if node_info and 'gpu_type' in node_info:
                    gpu_type = node_info['gpu_type']
                    for job in alloc_info['jobs']:
                        user_gpu_summary[job['user']][gpu_type]['count'] += job['gpus']
                        user_gpu_summary[job['user']][gpu_type]['jobs'].append({
                            'name': job['job'],
                            'id': job['jobid']
                        })
            
            queue_summary = defaultdict(lambda: {'jobs': 0, 'gpus': 0, 'users': 0})
            for job in queued_jobs:
                gpu_type = job['gpu_type']
                queue_summary[gpu_type]['jobs'] += 1
                queue_summary[gpu_type]['gpus'] += job['gpu_count']
            
            for gpu_type in queue_summary:
                users = set(job['user'] for job in queued_jobs if job['gpu_type'] == gpu_type)
                queue_summary[gpu_type]['users'] = len(users)
            
            db_logger.log_gpu_availability(dict(gpu_summary))
            db_logger.log_user_usage(dict(user_gpu_summary))
            db_logger.log_queue_status(dict(queue_summary))
            db_logger.log_node_status(nodes)
            db_logger.close()
            console.print(f"[green]Time series data saved to {args.db_path}[/green]")
        
        # Send Discord notification if requested
        if args.discord:
            gpu_summary = defaultdict(lambda: {'true_available': 0})
            user_gpu_summary = defaultdict(lambda: defaultdict(lambda: {'count': 0}))
            
            for node in nodes:
                if 'gpu_type' in node:
                    state = node.get('state', '')
                    if 'DRAIN' not in state and 'DOWN' not in state:
                        gpu_type = node['gpu_type']
                        total = node.get('gpu_total', 0)
                        used = node.get('gpu_used', 0)
                        gpu_summary[gpu_type]['true_available'] += (total - used)
            
            for node_name, alloc_info in allocations.items():
                node_info = next((n for n in nodes if n.get('name') == node_name), None)
                if node_info and 'gpu_type' in node_info:
                    gpu_type = node_info['gpu_type']
                    for job in alloc_info['jobs']:
                        user_gpu_summary[job['user']][gpu_type]['count'] += job['gpus']
            
            send_discord_notification(dict(gpu_summary), dict(user_gpu_summary))
            console.print("[green]Discord notification sent![/green]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")