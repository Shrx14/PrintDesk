# **PrintDesk** – Printer Data Analytics Platform

**PrintDesk** is a web-based application designed for **enterprise-level management** and **analysis of printer usage**. The platform allows administrators to track printing activity, monitor most and least used printers, and visualize print data across multiple departments with ease. The app leverages **Flask** as the backend, **MS SQL Server** for data storage, and supports **SSO login** for secure access.

---

## 🚀 **Features**

- **SSO Authentication** via Azure Active Directory
- **Role-Based Access**:
  - **Admin**: User and role management
  - **Uploader**: Can upload print data (via Excel)
  - **Viewer**: Can view analyzed data
- **Data Upload**: Upload Excel files with print logs
- **Data Analytics**: Reports on top printers, most-used users, and department-based analytics
- **Filters**: Date range filters (daily, weekly, monthly, yearly) and printer-specific filters
- **Admin Panel**: Admins can manage users and roles

---

## 🧑‍💻 **Tech Stack**

- **Frontend**: HTML, CSS (Bootstrap/Tailwind), JavaScript
- **Backend**: Python (Flask)
- **Database**: MS SQL Server
- **Excel Parsing**: pandas, openpyxl
- **SSO Authentication**: Azure Active Directory (OAuth2)
- **Visualization**: Static HTML-based analytics (Power BI will be integrated later)

---

## 💻 **Installation**

### 1. **Clone the repository**

```bash
git clone https://github.com/yourusername/PrintDesk.git
cd PrintDesk
