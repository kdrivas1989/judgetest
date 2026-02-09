# Deploy to Oracle Cloud Free Tier

## Part 1: Create Oracle Cloud Account & VM (10 minutes)

### 1.1 Create Account
1. Go to https://www.oracle.com/cloud/free/
2. Click "Start for free"
3. Fill in your details (requires credit card for verification - won't be charged)
4. Select home region closest to you (e.g., "US West (Phoenix)")
5. Wait for account activation email (~5 minutes)

### 1.2 Create Free VM
1. Log into Oracle Cloud Console: https://cloud.oracle.com
2. Click hamburger menu (☰) → **Compute** → **Instances**
3. Click **Create Instance**
4. Configure:
   - **Name**: `judgetest`
   - **Compartment**: Keep default
   - **Placement**: Keep default
   - **Image**: Click **Edit** → **Change Image** → Select **Ubuntu 22.04** (Canonical Ubuntu)
   - **Shape**: Click **Edit** → **Change Shape** → **Virtual Machine** → Select **VM.Standard.E2.1.Micro** (Always Free-eligible)

5. **Add SSH Keys** (IMPORTANT):
   - Select **Generate a key pair for me**
   - Click **Save Private Key** - save this file! You'll need it to connect
   - Or paste your existing public key if you have one

6. Click **Create**
7. Wait for instance to show "RUNNING" (~2 minutes)
8. Copy the **Public IP Address** shown

### 1.3 Open Firewall in Oracle Console
1. On your instance page, click the **Subnet** link (under Primary VNIC)
2. Click the **Security List** (e.g., "Default Security List for vcn-xxx")
3. Click **Add Ingress Rules**
4. Add this rule:
   - **Source CIDR**: `0.0.0.0/0`
   - **Destination Port Range**: `80,443`
   - **Description**: HTTP/HTTPS
5. Click **Add Ingress Rules**

---

## Part 2: Deploy the App (5 minutes)

### 2.1 Connect to Your VM

**On Mac/Linux:**
```bash
# Make the key file secure
chmod 400 ~/Downloads/ssh-key-*.key

# Connect (replace IP with your VM's public IP)
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_VM_IP
```

**On Windows (PowerShell):**
```powershell
ssh -i C:\Users\YOU\Downloads\ssh-key-*.key ubuntu@YOUR_VM_IP
```

### 2.2 Run the Deployment Script

Once connected to your VM, run this single command:

```bash
curl -sSL https://raw.githubusercontent.com/kdrivas1989/judgetest/main/deploy.sh | bash
```

The script will:
- Ask for your Supabase credentials (optional - can use SQLite)
- Install all dependencies
- Set up the app as a system service
- Configure nginx
- Start everything

### 2.3 Done!

Your app will be live at: `http://YOUR_VM_IP`

---

## Useful Commands

```bash
# View live logs
sudo journalctl -u judgetest -f

# Restart the app
sudo systemctl restart judgetest

# Check status
sudo systemctl status judgetest

# Update to latest code
cd ~/judgetest && git pull && sudo systemctl restart judgetest
```

---

## Optional: Add a Custom Domain

1. Point your domain's A record to your VM's IP
2. Install Certbot for free SSL:
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d yourdomain.com
```

---

## Troubleshooting

**Can't connect via SSH?**
- Make sure you're using the correct key file
- Check that the instance is "RUNNING"
- Verify your IP hasn't changed

**App not loading?**
- Check the firewall rules in Oracle Console
- Run: `sudo systemctl status judgetest`
- Check logs: `sudo journalctl -u judgetest -n 50`

**Port 80 blocked?**
- Oracle has TWO firewalls: Console security list AND iptables
- The deploy script handles iptables, but double-check Console rules
