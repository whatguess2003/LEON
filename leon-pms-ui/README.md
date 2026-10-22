# LEON PMS - Modern Hotel Management UI

A modern, responsive web-based Property Management System (PMS) interface that integrates seamlessly with the LEON hotel management backend and chat system.

## Features

### 🏨 **Modern Design**
- Clean, responsive design using Tailwind CSS
- Component-based architecture with Alpine.js
- Mobile-first approach with touch-friendly interfaces
- Real-time updates and animations

### 🤖 **LEON Chat Integration**
- Floating chat widget with context-aware messaging
- Quick action buttons for common tasks
- Natural language processing for hotel operations
- Real-time communication with LEON backend

### 📊 **Dashboard**
- Real-time hotel performance metrics
- Interactive charts and analytics
- Quick action buttons for common operations
- Recent activity and alerts monitoring
- Room status overview with visual indicators

### 🛏️ **Room Management**
- Visual room grid with status indicators
- Click-to-edit functionality for room properties
- Drag-and-drop room assignments
- Bulk operations for multiple rooms
- Advanced filtering and searching
- Real-time status updates

### 📅 **Booking Management**
- Interactive booking calendar
- Drag-and-drop booking assignments
- Guest profile integration
- Payment status tracking
- Automated check-in/check-out workflows

### 👥 **Guest Management**
- Comprehensive guest profiles
- Booking history and preferences
- Communication logs
- Loyalty program integration
- Advanced search capabilities

### 🔧 **Operations Dashboard**
- Daily operations workflow
- Housekeeping task management
- Maintenance request tracking
- Staff assignment and scheduling
- Check-in/check-out processing

### 📈 **Analytics & Reporting**
- Interactive charts and graphs
- Revenue and occupancy analytics
- Performance trend analysis
- Exportable reports
- LEON-powered insights

## Technology Stack

- **Frontend Framework**: Alpine.js (lightweight, reactive)
- **CSS Framework**: Tailwind CSS (utility-first)
- **Charts**: Chart.js (interactive visualizations)
- **Icons**: Font Awesome (comprehensive icon library)
- **Fonts**: Inter (modern, readable typography)
- **Backend API**: FastAPI (existing LEON backend)
- **Chat Integration**: WebSocket + REST API

## File Structure

```
leon-pms-ui/
├── index.html                 # Main application entry point
├── css/
│   └── modern-pms.css        # Custom styles and design system
├── js/
│   ├── pms-app.js            # Main application logic
│   └── components/
│       ├── dashboard.js      # Dashboard component
│       ├── rooms.js          # Room management component
│       ├── bookings.js       # Booking management component
│       ├── guests.js         # Guest management component
│       ├── operations.js     # Operations dashboard component
│       ├── analytics.js      # Analytics component
│       └── leon-chat.js      # LEON chat integration
└── README.md                 # This file
```

## Setup Instructions

### 1. **Backend Requirements**
Ensure your LEON backend is running:
- FastAPI server on `http://localhost:8000`
- LEON Chat server on `http://localhost:8002`
- Database connections (PostgreSQL + MongoDB)

### 2. **Frontend Setup**
1. Serve the HTML files using a web server:
   ```bash
   # Using Python
   python -m http.server 8080
   
   # Using Node.js
   npx http-server -p 8080
   
   # Using any other static file server
   ```

2. Open `http://localhost:8080` in your browser

### 3. **Configuration**
Update API endpoints in `js/pms-app.js`:
```javascript
// API Configuration
apiBase: 'http://localhost:8000/api/dashboard',
leonChatUrl: 'http://localhost:8002',
```

## Usage Guide

### **Dashboard Navigation**
- Use the top navigation bar to switch between different views
- Click on the LEON status indicator to check system health
- Use keyboard shortcuts (Alt+1-6) for quick navigation

### **Room Management**
- **Grid View**: Visual room cards with status indicators
- **List View**: Detailed room information in table format
- **Quick Actions**: Click on room cards for quick status updates
- **Bulk Operations**: Select multiple rooms for bulk actions
- **Search & Filter**: Find specific rooms using various criteria

### **LEON Chat Integration**
- **Floating Widget**: Always accessible chat interface
- **Context Awareness**: LEON understands your current view
- **Quick Actions**: Pre-defined buttons for common requests
- **Natural Language**: Ask questions in plain English

### **Booking Management**
- **Calendar View**: Visual booking calendar with drag-and-drop
- **Guest Assignment**: Assign rooms to guests with real-time validation
- **Payment Tracking**: Monitor payment status and processing
- **Automated Workflows**: Handle check-in/check-out processes

## Key Features

### **Real-time Updates**
- WebSocket connections for live data updates
- Automatic refresh of room status and bookings
- Real-time notifications for important events

### **Responsive Design**
- Mobile-friendly interface
- Touch-friendly controls
- Adaptive layout for different screen sizes

### **Accessibility**
- Keyboard navigation support
- Screen reader compatible
- High contrast mode available

### **Performance**
- Lazy loading for large datasets
- Optimized API calls
- Efficient DOM updates

## Integration with LEON

### **Chat Commands**
- `"Show me today's check-ins"` - Display pending arrivals
- `"Room availability status"` - Current room status overview
- `"Update room prices"` - Navigate to pricing management
- `"Generate occupancy report"` - Create analytics report

### **Context-Aware Actions**
- LEON understands your current view (dashboard, rooms, bookings)
- Provides relevant suggestions based on current data
- Executes actions directly in the UI

### **Automated Workflows**
- LEON can trigger UI updates based on backend events
- Automatic navigation to relevant sections
- Smart notifications and alerts

## Browser Support

- **Chrome**: 90+ (Recommended)
- **Firefox**: 88+
- **Safari**: 14+
- **Edge**: 90+

## Development

### **Adding New Components**
1. Create new component file in `js/components/`
2. Follow the existing component pattern
3. Register component in main application
4. Add navigation and routing logic

### **Customizing Styles**
- Modify CSS variables in the `:root` section
- Use Tailwind utility classes for rapid styling
- Custom components in `modern-pms.css`

### **API Integration**
- All API calls go through the main app's `apiRequest()` method
- Error handling and loading states included
- Consistent response formatting

## Troubleshooting

### **Common Issues**
1. **LEON Chat Not Working**: Check chat server URL and connectivity
2. **Data Not Loading**: Verify backend API endpoints are accessible
3. **Styling Issues**: Ensure Tailwind CSS is loaded properly
4. **Performance**: Enable browser caching for static assets

### **Debug Mode**
Enable debug logging in browser console:
```javascript
localStorage.setItem('leon-pms-debug', 'true');
```

## Future Enhancements

- [ ] Mobile app version
- [ ] Offline mode support
- [ ] Multi-language support
- [ ] Advanced reporting dashboard
- [ ] Integration with external systems
- [ ] Voice command support
- [ ] AI-powered recommendations

## Support

For technical support or feature requests:
- Check the LEON system documentation
- Contact the development team
- Submit issues through the project management system

---

**Built with ❤️ for modern hotel management** 