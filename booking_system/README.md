# LEON - AI-Powered Property Management System (PMS)

## 🏨 Overview

LEON is a comprehensive AI-powered Property Management System designed for Malaysian hotels. It combines traditional PMS functionality with advanced AI capabilities, natural language processing, and intelligent automation.

## 🏗️ Architecture

### **Dual Database Architecture**
- **PostgreSQL**: Transactional data (bookings, payments, availability, inventory)
- **MongoDB**: Content data (guest profiles, staff profiles, hotel information, manager context)

### **Natural Key System**
- **No artificial IDs**: Uses meaningful identifiers throughout
- **Hotel names**: `"ELLA Premium KLCC"` instead of `HOTEL001`
- **Guest phones**: `"+60123456789"` instead of `GUEST001`
- **Room types**: `"Executive Suite"` instead of `ROOM001`

### **AI Agent System**
- **Orchestrator**: Routes requests to specialized agents
- **Specialized Agents**: Handle specific hotel operations
- **Context-Aware**: Manager session and hotel-specific operations

## 🔐 Authentication System

### **Manager Authentication**
```bash
python hotel_context_manager.py
```

**3 Primary Authenticators:**
1. **Phone**: `+60123456789`
2. **Name**: `"John Smith"`
3. **Hotel**: `"ELLA Premium KLCC"`

**Features:**
- Pure authentication logic (no LLM)
- Session management via `manager_context.json`
- Cultural context and language preferences
- Role-based permissions

## 🛠️ Core Components

### **1. Hotel Context Manager**
- **File**: `hotel_context_manager.py`
- **Purpose**: Manager authentication and session management
- **Features**: Signup, signin (3 methods), signout, session tracking

### **2. LEON Meta Orchestrator**
- **File**: `leon_meta_orchestrator.py`
- **Purpose**: Main AI orchestrator and tool router
- **Features**: Context management, tool routing, dialog history

### **3. Specialized Managers**

#### **Booking Manager** (`booking_manager.py`)
- Create, read, update, delete bookings
- Availability checking
- Booking validation and confirmation

#### **Guest Manager** (`guest_manager.py`)
- Guest profile management
- Guest search and filtering
- Guest preferences and history

#### **Room Manager** (`room_manager.py`)
- Room type management
- Physical room assignments
- Room portfolio and pricing

#### **Inventory Manager** (`inventory_manager.py`)
- Room availability management
- Pricing strategies
- Inventory optimization

#### **Staff Manager** (`staff_manager.py`)
- Staff profile management
- Department assignments
- Performance tracking

#### **Payment Manager** (`payment_manager.py`)
- Payment processing
- Payment status tracking
- Payment deadlines

#### **Housekeeping Manager** (`housekeeping_manager.py`)
- Room preparation tracking
- Cleaning task assignments
- Room readiness status

#### **Maintenance Manager** (`maintenance_manager.py`)
- Maintenance request handling
- Issue tracking and resolution
- Maintenance scheduling

#### **Report Manager** (`report_manager.py`)
- Revenue analytics
- Occupancy reports
- Business intelligence

#### **Policy Manager** (`policy_manager.py`)
- Hotel policy management
- Booking policies
- Cancellation policies

#### **Front Desk Manager** (`front_desk_manager.py`)
- Check-in/check-out processes
- Room assignments
- Guest services

#### **Concierge Manager** (`concierge_manager.py`)
- Local information and recommendations
- Guest assistance
- City guides and attractions

## 📊 Database Structure

### **PostgreSQL Tables**
```sql
-- Core transactional data
bookings (booking_id, guest_phone, hotel_name, room_type, dates, status)
payments (payment_id, booking_id, amount, status, due_date)
atomic_availability (room_id, stay_date, quantity)
room_pricing (room_id, date, price)
```

### **MongoDB Collections**
```javascript
// Manager system
manager_collection: {
  manager_phone: "+60123456789",
  manager_name: "John Smith",
  hotel_name: "ELLA Premium KLCC",
  role: "General Manager",
  permissions: ["booking_management", "guest_management"],
  language_expertise: {...},
  cultural_context: {...}
}

// Guest profiles
guest_profiles: {
  guest_phone: "+60123456789",
  guest_name: "Ahmad Ali",
  preferences: {...},
  booking_history: [...]
}

// Staff profiles
staff_profiles: {
  staff_phone: "+60123456789",
  staff_name: "Sarah Tan",
  department: "Housekeeping",
  skills: [...]
}
```

## 🚀 Usage

### **1. Manager Authentication**
```bash
cd booking-system
python hotel_context_manager.py
```

### **2. AI Agent Interaction**
```python
from leon_meta_orchestrator import LEONMetaOrchestrator

# Initialize orchestrator
orchestrator = LEONMetaOrchestrator()

# Process requests
response = orchestrator.process_request("Create a booking for John Smith")
print(response)
```

### **3. Direct Tool Usage**
```python
from booking_manager import create_booking_tool
from guest_manager import create_guest_profile_tool

# Create guest profile
result = create_guest_profile_tool(
    guest_name="Ahmad Ali",
    guest_phone="+60123456789",
    email="ahmad@email.com"
)

# Create booking
result = create_booking_tool(
    guest_phone="+60123456789",
    hotel_name="ELLA Premium KLCC",
    room_type="Executive Suite",
    check_in="2024-01-15",
    check_out="2024-01-17"
)
```

## 🔧 Key Features

### **AI-Powered Operations**
- **Natural Language Processing**: Understand hotel requests in plain English
- **Context Awareness**: Remember conversation history and manager context
- **Intelligent Routing**: Automatically route requests to appropriate tools
- **Smart Validation**: Validate inputs against database constraints

### **Hotel-Specific Features**
- **Multi-Hotel Support**: Manage multiple hotels from single system
- **Cultural Context**: Malaysian hotel standards and cultural sensitivity
- **Language Support**: English and Bahasa Malaysia
- **Halal Awareness**: Built-in halal compliance features

### **Operational Excellence**
- **Real-time Availability**: Live room availability tracking
- **Automated Workflows**: Streamlined check-in/check-out processes
- **Performance Analytics**: Comprehensive reporting and insights
- **Mobile Ready**: Responsive design for mobile devices

## 🎯 Agent-Rule Compliance

### **CRUD Operations**
- **CREATE**: When no matching record exists
- **READ**: Always read database first for validation
- **UPDATE**: When exact match found
- **DELETE**: When exact match found

### **Validation Patterns**
1. **READ** database first
2. **Validate** user parameters
3. **NO MATCH** → CREATE operation
4. **PERFECT MATCH** → UPDATE/DELETE operation
5. **SIMILAR MATCH** → Show data and clarify with user

## 🔒 Security & Permissions

### **Manager Permissions**
- `booking_management`: Create and manage bookings
- `guest_management`: Access guest profiles and data
- `room_management`: Manage room types and assignments
- `reporting`: Access analytics and reports
- `staff_management`: Manage staff profiles
- `payment_management`: Process payments and refunds

### **Session Management**
- **Active Session**: Stored in `manager_context.json`
- **Context Locking**: Operations locked to manager's hotel
- **Session Timeout**: Automatic session cleanup
- **Audit Trail**: Complete operation logging

## 🌟 Advanced Features

### **Intelligent Automation**
- **Smart Room Assignment**: AI-powered room allocation
- **Dynamic Pricing**: Real-time pricing optimization
- **Predictive Analytics**: Occupancy forecasting
- **Automated Reporting**: Scheduled report generation

### **Guest Experience**
- **Personalized Service**: Guest preference tracking
- **Multi-language Support**: Communication in guest's language
- **Cultural Sensitivity**: Respect for cultural preferences
- **Seamless Integration**: Unified guest journey

### **Operational Efficiency**
- **Workflow Automation**: Streamlined processes
- **Real-time Updates**: Live data synchronization
- **Mobile Accessibility**: On-the-go management
- **Integration Ready**: API-first architecture

## 📈 Business Intelligence

### **Analytics Dashboard**
- **Revenue Analytics**: Comprehensive financial reporting
- **Occupancy Trends**: Historical and predictive analysis
- **Guest Insights**: Behavior and preference analysis
- **Performance Metrics**: KPI tracking and optimization

### **Reporting Capabilities**
- **Custom Reports**: Flexible report generation
- **Export Options**: Multiple format support
- **Scheduled Reports**: Automated delivery
- **Real-time Data**: Live dashboard updates

## 🔧 Technical Requirements

### **Environment Setup**
```bash
# Required environment variables
MONGO_URL=mongodb://localhost:27017/
OPENAI_API_KEY=your_openai_api_key
```

### **Dependencies**
```bash
pip install -r requirements.txt
```

### **Database Setup**
```bash
# PostgreSQL setup
python database_setup.py

# MongoDB setup
python manage_database.py
```

## 🚀 Getting Started

1. **Clone Repository**
   ```bash
   git clone <repository-url>
   cd booking-system
   ```

2. **Setup Environment**
   ```bash
   cp env.example .env
   # Edit .env with your configuration
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Initialize Databases**
   ```bash
   python database_setup.py
   python manage_database.py
   ```

5. **Start Authentication System**
   ```bash
   python hotel_context_manager.py
   ```

6. **Test AI Agent**
   ```bash
   python leon_meta_orchestrator.py
   ```

## 📞 Support

For technical support or questions about LEON:
- **Email**: support@leon-pms.com
- **Documentation**: [Full Documentation](https://docs.leon-pms.com)
- **API Reference**: [API Documentation](https://api.leon-pms.com)

---

**LEON - Revolutionizing Hotel Management with AI** 🏨✨ 