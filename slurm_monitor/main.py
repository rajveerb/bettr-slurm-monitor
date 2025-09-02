#!/usr/bin/env python3
"""
Slurm GPU Monitor - Terminal UI using Textual
A proper TUI implementation for better display handling
"""

import subprocess
import re
import sqlite3
import asyncio
import requests
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer, Center
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Header, Footer, DataTable, Static, Label, Button, TabbedContent, TabPane, LoadingIndicator
from textual.binding import Binding
from textual import events, work
from rich.text import Text
from rich.table import Table

class SlurmCommands:
    """Slurm command execution"""
    
    @staticmethod
    def get_node_info():
        """Get detailed node information from scontrol"""
        try:
            result = subprocess.run(['scontrol', 'show', 'node', '-d'], 
                                   capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return []
            
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
        except subprocess.TimeoutExpired:
            return []
        except Exception as e:
            return []
    
    @staticmethod
    def get_job_allocations():
        """Get current job allocations with user information"""
        try:
            result = subprocess.run(['squeue', '-o', '%N|%u|%T|%b|%j|%i'], 
                                   capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return {}
            
            allocations = defaultdict(lambda: {'users': set(), 'jobs': []})
            
            for line in result.stdout.split('\n')[1:]:
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
                    
                    if state == 'RUNNING' and 'gpu' in gres:
                        gpu_match = re.search(r'gpu:(\w+:)?(\d+)', gres)
                        if gpu_match:
                            gpu_count = int(gpu_match.group(2))
                            
                            # For job allocations, we need to handle per-node GPU count differently
                            # The GPU count in squeue is total for the job, not per node
                            nodes = SlurmCommands.expand_nodelist(nodelist)
                            gpu_per_node = gpu_count // len(nodes) if len(nodes) > 0 else gpu_count
                            for node in nodes:
                                allocations[node]['users'].add(user)
                                allocations[node]['jobs'].append({
                                    'user': user,
                                    'job': jobname,
                                    'jobid': jobid,
                                    'gpus': gpu_per_node
                                })
            
            return allocations
        except Exception as e:
            return {}
    
    @staticmethod
    def get_queued_jobs():
        """Get queued jobs information"""
        try:
            result = subprocess.run(['squeue', '-o', '%u|%T|%b|%j|%i|%Q|%S|%l'], 
                                   capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return []
            
            queued_jobs = []
            
            for line in result.stdout.split('\n')[1:]:
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
                    time_limit = parts[7] if len(parts) > 7 else '1:00:00'
                    
                    if state == 'PENDING' and 'gpu' in gres:
                        gpu_match = re.search(r'gpu:(\w+:)?(\d+)', gres)
                        if gpu_match:
                            gpu_type = gpu_match.group(1).rstrip(':') if gpu_match.group(1) else 'Any'
                            gpu_count = int(gpu_match.group(2))
                            
                            # Parse time limit to hours
                            gpu_hours = SlurmCommands.parse_time_to_hours(time_limit) * gpu_count
                            
                            queued_jobs.append({
                                'user': user,
                                'job': jobname,
                                'jobid': jobid,
                                'gpu_type': gpu_type,
                                'gpu_count': gpu_count,
                                'gpu_hours': gpu_hours,
                                'priority': priority,
                                'estimated_start': start_time
                            })
            
            return queued_jobs
        except Exception as e:
            return []
    
    @staticmethod
    def expand_nodelist(nodelist):
        """Expand Slurm nodelist format to individual nodes"""
        try:
            result = subprocess.run(['scontrol', 'show', 'hostname', nodelist],
                                   capture_output=True, text=True, timeout=5)
            return result.stdout.strip().split('\n')
        except:
            return [nodelist]
    
    @staticmethod
    def parse_time_to_hours(time_str):
        """Parse Slurm time format to hours"""
        try:
            # Handle various time formats: D-HH:MM:SS, HH:MM:SS, MM:SS
            if '-' in time_str:
                days, time = time_str.split('-')
                days = int(days)
            else:
                days = 0
                time = time_str
            
            parts = time.split(':')
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
            elif len(parts) == 2:
                hours = 0
                minutes = int(parts[0])
            else:
                return 1.0  # Default to 1 hour
            
            total_hours = days * 24 + hours + minutes / 60
            return total_hours if total_hours > 0 else 1.0
        except:
            return 1.0  # Default to 1 hour on parse error

class OverviewWidget(Vertical):
    """Overview page widget"""
    
    def compose(self) -> ComposeResult:
        yield Label("📊 GPU Overview - Quick Availability", classes="title")
        with Center():
            yield LoadingIndicator(id="overview-loading")
        yield DataTable(id="overview-table", show_cursor=False)
        yield Label("", id="overview-status", classes="status")
        yield Label("🔥 Heavy Users (Current GPU Usage)", classes="subtitle")
        yield DataTable(id="overview-users-table", show_cursor=False)
    
    def update_data(self, nodes: list, allocations: dict):
        """Update the overview display"""
        # Hide loading, show tables
        self.query_one("#overview-loading").display = False
        table = self.query_one("#overview-table", DataTable)
        table.display = True
        users_table = self.query_one("#overview-users-table", DataTable)
        users_table.display = True
        
        # Clear and setup table
        table.clear(columns=True)
        table.add_column("GPU Type", width=12)
        table.add_column("Total", width=8)
        table.add_column("Used", width=8)
        table.add_column("Available", width=12)
        table.add_column("Usage %", width=10)
        table.add_column("Healthy Nodes", width=15)
        
        # Calculate summary
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
        
        # Add rows
        total_available = 0
        for gpu_type in sorted(gpu_summary.keys()):
            info = gpu_summary[gpu_type]
            usage_pct = (info['used'] / info['total'] * 100) if info['total'] > 0 else 0
            healthy = info['nodes'] - info['drain_nodes']
            total_available += info['true_available']
            
            # Color code availability
            avail_str = f"{info['true_available']}"
            if info['true_available'] > 0:
                avail_str = f"✅ {info['true_available']}"
            else:
                avail_str = f"❌ {info['true_available']}"
            
            table.add_row(
                gpu_type,
                str(info['total']),
                str(info['used']),
                avail_str,
                f"{usage_pct:.1f}%",
                f"{healthy}/{info['nodes']}"
            )
        
        # Update status
        status = self.query_one("#overview-status", Label)
        if total_available > 0:
            status.update(f"✅ Total GPUs Available: {total_available}")
            status.add_class("success")
        else:
            status.update("❌ No GPUs Currently Available")
            status.add_class("warning")
        
        # Add heavy users table
        users_table.clear(columns=True)
        users_table.add_column("User", width=20)
        users_table.add_column("GPU Type", width=12)
        users_table.add_column("GPUs Used", width=10)
        users_table.add_column("Nodes", width=30)
        
        # Calculate user GPU usage
        user_gpu_summary = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'nodes': set()}))
        
        # Debug: Check if allocations has data
        if allocations:
            for node_name, alloc_info in allocations.items():
                # Find node info to get GPU type
                node_info = next((n for n in nodes if n.get('name') == node_name), None)
                if node_info and 'gpu_type' in node_info:
                    gpu_type = node_info['gpu_type']
                    for job in alloc_info.get('jobs', []):
                        user_gpu_summary[job['user']][gpu_type]['count'] += job['gpus']
                        user_gpu_summary[job['user']][gpu_type]['nodes'].add(node_name)
        
        # Sort users by total GPU usage
        user_totals = {user: sum(gpu_data['count'] for gpu_data in gpus.values()) 
                       for user, gpus in user_gpu_summary.items()}
        
        # Show top 5 heavy users
        for user in sorted(user_totals.keys(), key=lambda u: user_totals[u], reverse=True)[:5]:
            for gpu_type in sorted(user_gpu_summary[user].keys()):
                gpu_data = user_gpu_summary[user][gpu_type]
                nodes_str = ', '.join(sorted(list(gpu_data['nodes']))[:3])
                if len(gpu_data['nodes']) > 3:
                    nodes_str += f" (+{len(gpu_data['nodes'])-3} more)"
                
                users_table.add_row(
                    user,
                    gpu_type,
                    str(gpu_data['count']),
                    nodes_str
                )
        
        if not user_gpu_summary:
            # If no users found, show a message
            users_table.add_row("No active GPU users", "-", "-", "-")
    
    def show_loading(self):
        """Show loading indicator"""
        self.query_one("#overview-loading").display = True
        self.query_one("#overview-table").display = False
        self.query_one("#overview-users-table").display = False

class NodesWidget(Vertical):
    """Nodes page widget"""
    
    def compose(self) -> ComposeResult:
        yield Label("🖥️ Node Details", classes="title")
        with Center():
            yield LoadingIndicator(id="nodes-loading")
        yield DataTable(id="nodes-table", show_cursor=False)
    
    def update_data(self, nodes: list, allocations: dict):
        """Update the nodes display"""
        self.query_one("#nodes-loading").display = False
        table = self.query_one("#nodes-table", DataTable)
        table.display = True
        
        table.clear(columns=True)
        table.add_column("Node", width=20)
        table.add_column("State", width=15)
        table.add_column("GPU Type", width=10)
        table.add_column("Total", width=8)
        table.add_column("Used", width=8)
        table.add_column("Available", width=10)
        table.add_column("Users", width=30)
        
        for node in sorted(nodes, key=lambda x: x.get('name', '')):
            if 'gpu_type' in node:
                total = node.get('gpu_total', 0)
                used = node.get('gpu_used', 0)
                available = total - used
                state = node.get('state', '')
                
                # Color code state
                if 'IDLE' in state:
                    state_str = f"🟢 {state}"
                elif 'ALLOCATED' in state or 'MIXED' in state:
                    state_str = f"🟡 {state}"
                elif 'DRAIN' in state:
                    state_str = f"🔴 {state}"
                elif 'DOWN' in state:
                    state_str = f"⚫ {state}"
                else:
                    state_str = state
                
                users = ', '.join(sorted(allocations.get(node['name'], {}).get('users', [])))
                if not users:
                    users = '-'
                
                table.add_row(
                    node['name'],
                    state_str,
                    node['gpu_type'],
                    str(total),
                    str(used),
                    str(available),
                    users
                )
    
    def show_loading(self):
        """Show loading indicator"""
        self.query_one("#nodes-loading").display = True
        self.query_one("#nodes-table").display = False

class QueueWidget(Vertical):
    """Queue page widget"""
    
    def compose(self) -> ComposeResult:
        yield Label("📋 Job Queue", classes="title")
        with Center():
            yield LoadingIndicator(id="queue-loading")
        yield Label("⏳ PENDING Jobs - Queue by GPU Type:", classes="subtitle")
        yield DataTable(id="queue-summary-table", show_cursor=False)
        yield Label("⏳ PENDING Jobs - Queue by User (Top 10):", classes="subtitle")
        yield DataTable(id="queue-users-table", show_cursor=False)
    
    def update_data(self, queued_jobs: list):
        """Update the queue display"""
        # Hide loading, show both tables
        self.query_one("#queue-loading").display = False
        
        # Summary table
        summary_table = self.query_one("#queue-summary-table", DataTable)
        summary_table.display = True
        summary_table.clear(columns=True)
        
        summary_table.add_column("GPU Type", width=15)
        summary_table.add_column("Pending Jobs", width=12)
        summary_table.add_column("GPUs Requested", width=12)
        summary_table.add_column("GPU Hours", width=12)
        summary_table.add_column("Unique Users", width=12)
        
        # Aggregate data
        gpu_type_summary = defaultdict(lambda: {'jobs': 0, 'gpus': 0, 'gpu_hours': 0, 'users': set()})
        user_queue_summary = defaultdict(lambda: defaultdict(lambda: {'jobs': 0, 'gpus': 0, 'gpu_hours': 0}))
        
        for job in queued_jobs:
            gpu_type = job['gpu_type']
            user = job['user']
            gpu_hours = job.get('gpu_hours', job['gpu_count'])
            
            gpu_type_summary[gpu_type]['jobs'] += 1
            gpu_type_summary[gpu_type]['gpus'] += job['gpu_count']
            gpu_type_summary[gpu_type]['gpu_hours'] += gpu_hours
            gpu_type_summary[gpu_type]['users'].add(user)
            
            user_queue_summary[user][gpu_type]['jobs'] += 1
            user_queue_summary[user][gpu_type]['gpus'] += job['gpu_count']
            user_queue_summary[user][gpu_type]['gpu_hours'] += gpu_hours
        
        # Add summary rows
        if gpu_type_summary:
            for gpu_type in sorted(gpu_type_summary.keys()):
                info = gpu_type_summary[gpu_type]
                summary_table.add_row(
                    gpu_type,
                    f"⏳ {info['jobs']}",
                    str(info['gpus']),
                    f"{info['gpu_hours']:.1f}",
                    str(len(info['users']))
                )
        else:
            summary_table.add_row("✅ No pending jobs", "-", "-", "-", "-")
        
        # Users table
        users_table = self.query_one("#queue-users-table", DataTable)
        users_table.display = True
        users_table.clear(columns=True)
        
        users_table.add_column("User", width=20)
        users_table.add_column("GPU Type", width=12)
        users_table.add_column("Pending Jobs", width=12)
        users_table.add_column("GPUs", width=8)
        users_table.add_column("GPU Hours", width=12)
        
        if user_queue_summary:
            # Sort users by total GPU hours requested
            user_totals = {user: sum(gpu['gpu_hours'] for gpu in gpus.values()) 
                           for user, gpus in user_queue_summary.items()}
            
            for user in sorted(user_totals.keys(), key=lambda u: user_totals[u], reverse=True)[:10]:
                for gpu_type in sorted(user_queue_summary[user].keys()):
                    data = user_queue_summary[user][gpu_type]
                    users_table.add_row(
                        user,
                        gpu_type,
                        f"⏳ {data['jobs']}",
                        str(data['gpus']),
                        f"{data['gpu_hours']:.1f}"
                    )
        else:
            users_table.add_row("No queued jobs", "-", "-", "-", "-")
    
    def show_loading(self):
        """Show loading indicator"""
        self.query_one("#queue-loading").display = True
        self.query_one("#queue-summary-table").display = False
        self.query_one("#queue-users-table").display = False

class SlurmMonitorApp(App):
    """Main TUI application"""
    
    CSS = """
    Screen {
        background: $surface;
    }
    
    DataTable {
        height: auto;
        max-height: 20;
        margin: 1;
    }
    
    LoadingIndicator {
        height: 3;
        margin: 2;
    }
    
    .title {
        padding: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        text-align: center;
    }
    
    .subtitle {
        padding-top: 1;
        text-style: bold;
        color: $primary;
    }
    
    .status {
        padding: 1;
        margin: 1;
        text-align: center;
    }
    
    .success {
        color: $success;
    }
    
    .warning {
        color: $warning;
    }
    
    TabbedContent {
        height: 100%;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "show_tab('overview')", "Overview"),
        Binding("2", "show_tab('nodes')", "Nodes"),
        Binding("3", "show_tab('queue')", "Queue"),
        Binding("up,down", "", "Scroll ↑↓"),
        Binding("pageup,pagedown", "", "Page ↑↓"),
    ]
    
    def __init__(self, db_path: Optional[str] = None, refresh_interval: int = 30, 
                 webhook_url: Optional[str] = None):
        super().__init__()
        self.db_path = db_path
        self.refresh_interval = refresh_interval
        self.webhook_url = webhook_url
        self.nodes = []
        self.allocations = {}
        self.queued_jobs = []
        self.last_discord_notify = None
        self.discord_interval = 1800  # 30 minutes default
        
        # Don't create DB connection in main thread if using threads
        if self.db_path:
            self.setup_database_schema()
    
    def setup_database_schema(self):
        """Setup SQLite database schema (creates tables if needed)"""
        if self.db_path:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
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
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS queue_status (
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    gpu_type TEXT,
                    queued_jobs INTEGER,
                    queued_gpus INTEGER,
                    unique_users INTEGER
                )
            ''')
            
            conn.commit()
            conn.close()
    
    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Header(show_clock=True)
        
        with TabbedContent(initial="overview"):
            with TabPane("Overview", id="overview"):
                yield OverviewWidget()
            with TabPane("Nodes", id="nodes"):
                yield NodesWidget()
            with TabPane("Queue", id="queue"):
                yield QueueWidget()
        
        yield Footer()
    
    async def on_mount(self) -> None:
        """Called when app starts"""
        # Show loading initially
        self.show_all_loading()
        # Start background refresh
        self.refresh_data_worker()
        # Set up periodic refresh
        self.set_interval(self.refresh_interval, self.refresh_data_worker)
    
    def show_all_loading(self):
        """Show loading indicators on all widgets"""
        for widget in self.query(OverviewWidget):
            widget.show_loading()
        for widget in self.query(NodesWidget):
            widget.show_loading()
        for widget in self.query(QueueWidget):
            widget.show_loading()
    
    @work(thread=True)
    def refresh_data_worker(self) -> None:
        """Refresh all data from Slurm in background"""
        # Fetch data
        self.nodes = SlurmCommands.get_node_info()
        self.allocations = SlurmCommands.get_job_allocations()
        self.queued_jobs = SlurmCommands.get_queued_jobs()
        
        # Update UI in main thread
        self.call_from_thread(self.update_ui)
        
        # Log to database if enabled
        if self.db_path:
            self.log_to_database()
        
        # Send Discord notification if enabled
        if self.webhook_url:
            self.send_discord_notification()
    
    def update_ui(self):
        """Update all widgets with new data"""
        # Update all widgets
        for widget in self.query(OverviewWidget):
            widget.update_data(self.nodes, self.allocations)
        for widget in self.query(NodesWidget):
            widget.update_data(self.nodes, self.allocations)
        for widget in self.query(QueueWidget):
            widget.update_data(self.queued_jobs)
    
    def log_to_database(self):
        """Log current state to database"""
        if not self.db_path:
            return
        
        # Create connection in worker thread
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timestamp = datetime.now()
        
        # Calculate and log GPU availability
        gpu_summary = defaultdict(lambda: {
            'total': 0, 'used': 0, 'true_available': 0, 'nodes': 0, 'drain_nodes': 0
        })
        
        for node in self.nodes:
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
        
        # Log user usage
        user_gpu_summary = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'jobs': 0}))
        
        for node_name, alloc_info in self.allocations.items():
            # Find node info to get GPU type
            node_info = next((n for n in self.nodes if n.get('name') == node_name), None)
            if node_info and 'gpu_type' in node_info:
                gpu_type = node_info['gpu_type']
                for job in alloc_info.get('jobs', []):
                    user_gpu_summary[job['user']][gpu_type]['count'] += job['gpus']
                    user_gpu_summary[job['user']][gpu_type]['jobs'] += 1
        
        for user, gpu_data in user_gpu_summary.items():
            for gpu_type, counts in gpu_data.items():
                cursor.execute('''
                    INSERT INTO user_usage
                    (timestamp, user, gpu_type, gpu_count, job_count)
                    VALUES (?, ?, ?, ?, ?)
                ''', (timestamp, user, gpu_type, counts['count'], counts['jobs']))
        
        # Log queue status
        queue_summary = defaultdict(lambda: {'jobs': 0, 'gpus': 0, 'users': set()})
        
        for job in self.queued_jobs:
            gpu_type = job['gpu_type']
            queue_summary[gpu_type]['jobs'] += 1
            queue_summary[gpu_type]['gpus'] += job['gpu_count']
            queue_summary[gpu_type]['users'].add(job['user'])
        
        for gpu_type, info in queue_summary.items():
            cursor.execute('''
                INSERT INTO queue_status
                (timestamp, gpu_type, queued_jobs, queued_gpus, unique_users)
                VALUES (?, ?, ?, ?, ?)
            ''', (timestamp, gpu_type, info['jobs'], info['gpus'], len(info['users'])))
        
        # Log node status
        for node in self.nodes:
            if 'gpu_type' in node:
                cursor.execute('''
                    INSERT INTO node_status
                    (timestamp, node_name, state, gpu_type, total_gpus, used_gpus)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp,
                    node.get('name', 'unknown'),
                    node.get('state', 'unknown'),
                    node['gpu_type'],
                    node.get('gpu_total', 0),
                    node.get('gpu_used', 0)
                ))
        
        conn.commit()
        conn.close()
    
    def send_discord_notification(self):
        """Send status update to Discord webhook"""
        if not self.webhook_url:
            return
        
        # Check if enough time has passed since last notification
        now = datetime.now()
        if self.last_discord_notify:
            time_diff = (now - self.last_discord_notify).total_seconds()
            if time_diff < self.discord_interval:
                return
        
        self.last_discord_notify = now
        
        # Calculate GPU summary
        gpu_summary = defaultdict(lambda: {'total': 0, 'used': 0, 'available': 0})
        
        for node in self.nodes:
            if 'gpu_type' in node:
                gpu_type = node['gpu_type']
                total = node.get('gpu_total', 0)
                used = node.get('gpu_used', 0)
                state = node.get('state', '')
                
                gpu_summary[gpu_type]['total'] += total
                gpu_summary[gpu_type]['used'] += used
                
                is_healthy = 'DRAIN' not in state and 'DOWN' not in state
                if is_healthy:
                    gpu_summary[gpu_type]['available'] += (total - used)
        
        # Build Discord embed
        embed = {
            "title": "🖥️ GPU Cluster Status Update",
            "color": 3447003,
            "timestamp": now.isoformat(),
            "fields": []
        }
        
        # Add GPU availability fields
        for gpu_type in sorted(gpu_summary.keys()):
            info = gpu_summary[gpu_type]
            usage_pct = (info['used'] / info['total'] * 100) if info['total'] > 0 else 0
            
            field = {
                "name": f"{gpu_type} GPUs",
                "value": f"Available: {info['available']}/{info['total']} ({usage_pct:.1f}% used)",
                "inline": True
            }
            embed["fields"].append(field)
        
        # Add queue summary
        total_queued = len(self.queued_jobs)
        if total_queued > 0:
            queue_gpus = sum(job['gpu_count'] for job in self.queued_jobs)
            embed["fields"].append({
                "name": "📋 Queue Status",
                "value": f"{total_queued} jobs waiting for {queue_gpus} GPUs",
                "inline": False
            })
        
        # Send webhook
        try:
            response = requests.post(
                self.webhook_url,
                json={"embeds": [embed]},
                timeout=10
            )
            response.raise_for_status()
        except Exception as e:
            # Silently fail - don't interrupt monitoring
            pass
    
    def action_refresh(self) -> None:
        """Handle refresh action"""
        self.show_all_loading()
        self.refresh_data_worker()
    
    def action_show_tab(self, tab: str) -> None:
        """Switch to a specific tab"""
        tabbed_content = self.query_one(TabbedContent)
        tabbed_content.active = tab

def main():
    """Entry point for TUI version"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Slurm GPU Monitor - TUI Version')
    parser.add_argument('--db', action='store_true', help='Enable SQLite logging')
    parser.add_argument('--db-path', type=str, default='gpu_monitor.db', help='Database path')
    parser.add_argument('--interval', type=int, default=30, help='Refresh interval in seconds')
    parser.add_argument('--webhook', type=str, help='Discord webhook URL for notifications')
    
    args = parser.parse_args()
    
    # Check environment variable for webhook if not provided as argument
    webhook_url = args.webhook or os.environ.get('DISCORD_WEBHOOK_URL')
    
    app = SlurmMonitorApp(
        db_path=args.db_path if args.db else None,
        refresh_interval=args.interval,
        webhook_url=webhook_url
    )
    app.run()

if __name__ == "__main__":
    main()