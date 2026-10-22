// Add local/production API URL switcher
const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const apiUrl = isLocal ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';

// Analytics Component
class AnalyticsComponent {
    constructor() {
        this.apiUrl = apiUrl;
        this.dateRange = 30; // Default to last 30 days
        this.charts = {};
        
        this.initialize();
    }
    
    async initialize() {
        await this.loadAnalyticsData();
        this.updateKPIs();
        this.initializeCharts();
        this.renderDemographics();
        this.renderReportsTable();
    }
    
    async loadAnalyticsData() {
        this.data = {
            kpis: { totalRevenue: 0, occupancyRate: 0, adr: 0, guestSatisfaction: null, changes: { revenue: 0, occupancy: 0, adr: 0, satisfaction: 0 } },
            revenue: [], occupancy: [], roomTypes: [], bookingSources: [], demographics: []
        };
        try {
            const response = await fetch(`${this.apiUrl}/analytics?days=${this.dateRange}`, { credentials: 'include' });
            if (response.ok) {
                const body = await response.json();
                if (body && body.kpis) this.data = body;
            }
        } catch (error) {
            console.error('Error loading analytics data:', error);
        }
    }
    
    updateKPIs() {
        const kpis = this.data.kpis;
        
        document.getElementById('total-revenue').textContent = `RM${Number(kpis.totalRevenue || 0).toLocaleString()}`;
        document.getElementById('occupancy-rate').textContent = `${Number(kpis.occupancyRate || 0).toFixed(1)}%`;
        document.getElementById('adr').textContent = `RM${Number(kpis.adr || 0).toFixed(0)}`;
        document.getElementById('satisfaction-score').textContent = kpis.guestSatisfaction == null ? '—' : `${Number(kpis.guestSatisfaction).toFixed(1)}/5`;
        
        // Update change indicators
        const changes = kpis.changes;
        document.getElementById('revenue-change').textContent = `${changes.revenue > 0 ? '+' : ''}${changes.revenue.toFixed(1)}% vs last period`;
        document.getElementById('occupancy-change').textContent = `${changes.occupancy > 0 ? '+' : ''}${changes.occupancy.toFixed(1)}% vs last period`;
        document.getElementById('adr-change').textContent = `${changes.adr > 0 ? '+' : ''}${changes.adr.toFixed(1)}% vs last period`;
        document.getElementById('satisfaction-change').textContent = `${changes.satisfaction > 0 ? '+' : ''}${changes.satisfaction.toFixed(1)} vs last period`;
    }
    
    initializeCharts() {
        this.createRevenueChart();
        this.createOccupancyChart();
        this.createRoomTypeChart();
        this.createBookingSourceChart();
    }
    
    createRevenueChart() {
        const ctx = document.getElementById('revenue-chart').getContext('2d');
        
        this.charts.revenue = new Chart(ctx, {
            type: 'line',
            data: {
                labels: this.data.revenue.map(item => this.formatDate(item.date)),
                datasets: [{
                    label: 'Daily Revenue',
                    data: this.data.revenue.map(item => item.revenue),
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    tension: 0.4,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return 'RM' + value.toLocaleString();
                            }
                        }
                    }
                }
            }
        });
    }
    
    createOccupancyChart() {
        const ctx = document.getElementById('occupancy-chart').getContext('2d');
        
        this.charts.occupancy = new Chart(ctx, {
            type: 'line',
            data: {
                labels: this.data.occupancy.map(item => this.formatDate(item.date)),
                datasets: [{
                    label: 'Occupancy Rate',
                    data: this.data.occupancy.map(item => item.occupancy),
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    tension: 0.4,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        ticks: {
                            callback: function(value) {
                                return value + '%';
                            }
                        }
                    }
                }
            }
        });
    }
    
    createRoomTypeChart() {
        const ctx = document.getElementById('room-type-chart').getContext('2d');
        
        this.charts.roomType = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: this.data.roomTypes.map(item => item.type),
                datasets: [{
                    data: this.data.roomTypes.map(item => item.revenue),
                    backgroundColor: [
                        '#3b82f6',
                        '#10b981',
                        '#f59e0b',
                        '#ef4444'
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }
    
    createBookingSourceChart() {
        const ctx = document.getElementById('booking-source-chart').getContext('2d');
        
        this.charts.bookingSource = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: this.data.bookingSources.map(item => item.source),
                datasets: [{
                    label: 'Bookings',
                    data: this.data.bookingSources.map(item => item.bookings),
                    backgroundColor: [
                        '#3b82f6',
                        '#10b981',
                        '#f59e0b',
                        '#ef4444',
                        '#8b5cf6'
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    }
    
    renderDemographics() {
        const demographicsList = document.getElementById('demographics-list');
        
        const demographicsHTML = this.data.demographics.map(demo => `
            <div class="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                <div class="flex items-center">
                    <div class="w-8 h-8 bg-blue-100 rounded-full flex items-center justify-center mr-3">
                        <i class="fas fa-flag text-blue-600 text-xs"></i>
                    </div>
                    <div>
                        <p class="font-medium text-gray-900">${demo.country}</p>
                        <p class="text-sm text-gray-500">${demo.guests} guests</p>
                    </div>
                </div>
                <div class="text-right">
                    <p class="font-semibold text-gray-900">${demo.percentage}%</p>
                </div>
            </div>
        `).join('');
        
        demographicsList.innerHTML = demographicsHTML;
    }
    
    renderReportsTable() {
        const tableBody = document.getElementById('reports-table');
        
        const roomCount = Number((this.data.kpis && this.data.kpis.roomCount) || 0);
        const occ = this.data.occupancy || [];
        const reportData = this.data.revenue.slice(-7).map((item, index) => {
            const occRow = occ[occ.length - Math.min(7, this.data.revenue.length) + index] || {};
            const occupancy = Number(occRow.occupancy || 0);
            const occupied = roomCount ? Math.round(roomCount * occupancy / 100) : 0;
            return {
                date: item.date,
                revenue: Number(item.revenue || 0),
                occupancy: occupancy,
                adr: occupied ? Number(item.revenue || 0) / occupied : 0,
                bookings: occupied,
                rating: null
            };
        });
        
        const tableHTML = reportData.map(row => `
            <tr class="border-b hover:bg-gray-50">
                <td class="py-3 px-4 text-gray-900">${this.formatDate(row.date)}</td>
                <td class="py-3 px-4 text-gray-900">RM${row.revenue.toLocaleString()}</td>
                <td class="py-3 px-4 text-gray-900">${row.occupancy.toFixed(1)}%</td>
                <td class="py-3 px-4 text-gray-900">RM${row.adr.toFixed(0)}</td>
                <td class="py-3 px-4 text-gray-900">${Math.round(row.bookings)}</td>
                <td class="py-3 px-4 text-gray-900">${row.rating == null ? '—' : row.rating.toFixed(1) + '/5'}</td>
            </tr>
        `).join('');
        
        tableBody.innerHTML = tableHTML;
    }
    
    async updateDateRange(days) {
        this.dateRange = days;
        await this.loadAnalyticsData();
        this.updateKPIs();
        
        // Update charts with new data
        this.updateChartData();
        this.renderDemographics();
        this.renderReportsTable();
    }
    
    updateChartData() {
        // Update revenue chart
        this.charts.revenue.data.labels = this.data.revenue.map(item => this.formatDate(item.date));
        this.charts.revenue.data.datasets[0].data = this.data.revenue.map(item => item.revenue);
        this.charts.revenue.update();
        
        // Update occupancy chart
        this.charts.occupancy.data.labels = this.data.occupancy.map(item => this.formatDate(item.date));
        this.charts.occupancy.data.datasets[0].data = this.data.occupancy.map(item => item.occupancy);
        this.charts.occupancy.update();
        
        // Update room type chart
        this.charts.roomType.data.labels = this.data.roomTypes.map(item => item.type);
        this.charts.roomType.data.datasets[0].data = this.data.roomTypes.map(item => item.revenue);
        this.charts.roomType.update();
        
        // Update booking source chart
        this.charts.bookingSource.data.labels = this.data.bookingSources.map(item => item.source);
        this.charts.bookingSource.data.datasets[0].data = this.data.bookingSources.map(item => item.bookings);
        this.charts.bookingSource.update();
    }

    downloadCsv(name, lines) {
        const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
    }

    exportReport() {
        const lines = ['date,revenue,occupancy'].concat((this.data.revenue || []).map((row, i) => {
            const occ = (this.data.occupancy || [])[i];
            return [row.date, row.revenue, occ ? occ.occupancy : ''].join(',');
        }));
        this.downloadCsv('analytics.csv', lines);
    }

    generateReport(reportType) {
        if (reportType === 'revenue') {
            this.downloadCsv('revenue.csv', ['date,revenue'].concat((this.data.revenue || []).map(row => row.date + ',' + row.revenue)));
            return;
        }
        if (reportType === 'occupancy') {
            this.downloadCsv('occupancy.csv', ['date,occupancy'].concat((this.data.occupancy || []).map(row => row.date + ',' + row.occupancy)));
            return;
        }
        this.downloadCsv('guests.csv', ['country,guests,percentage'].concat((this.data.demographics || []).map(row => row.country + ',' + row.guests + ',' + row.percentage)));
    }
    
    formatDate(dateStr) {
        const date = new Date(dateStr + 'T00:00:00');
        return date.toLocaleDateString('en-US', { 
            month: 'short', 
            day: 'numeric' 
        });
    }
} 