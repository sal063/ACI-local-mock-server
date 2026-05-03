ACI mock listener — quick start
================================

Goal: capture and answer the HTTP/HTTPS calls Ace Combat Infinity makes to
dev-wind.siliconstudio.co.jp so the game gets past the "Failed to connect to
PSN" dialog.

ONE-TIME SETUP
--------------

1. Install the Python dependency (only once):

       pip install cryptography

2. Add the redirect to your Windows hosts file. Open Notepad AS ADMIN, then
   File -> Open:

       C:\Windows\System32\drivers\etc\hosts

   Add this line at the bottom and save:

       127.0.0.1   dev-wind.siliconstudio.co.jp

   To verify, in PowerShell:

       Resolve-DnsName dev-wind.siliconstudio.co.jp
       (it should resolve to 127.0.0.1)

   Note: ports 80 and 443 must be free. If something else is using them
   (IIS, Skype, another web server), stop that first.

EACH TIME YOU PLAY
------------------

3. Open a PowerShell window AS ADMINISTRATOR (binding ports 80/443 needs
   admin privileges). Run:

       cd "C:\ext\OPus\New folder\ACI\listener"
       python aci_listener.py

   First run will auto-generate cert.pem / key.pem (self-signed for
   dev-wind.siliconstudio.co.jp).

   You should see:
       [http]  listening on 0.0.0.0:80
       [https] listening on 0.0.0.0:443

4. Boot ACI in RPCS3. Watch the listener console — every request the game
   makes is logged to the console and to requests.log.

5. After the run, share / read requests.log to see what the game asked for
   and what it sent. We then iterate STUB_RESPONSES in aci_listener.py to
   return realistic data.


If RPCS3 logs an SSL/cert error
-------------------------------
The PS3 libssl validation against our self-signed cert may fail. Two options:

  a) Try with HTTP only first. Some endpoints fall back to plain http;
     comment out the `serve_https` thread and see if that's enough to get
     past the dialog.

  b) Add cert.pem to RPCS3's CA bundle:
       rpcs3-...\dev_flash\data\cert\CA_LIST.cer
     by appending the PEM contents (or replacing). Back it up first.

TROUBLESHOOTING
---------------

- "Address already in use": port 80 or 443 is taken. Find the offender:
       netstat -ano | findstr ":80 "
       netstat -ano | findstr ":443 "
  Kill via Task Manager by PID, or change the port and adjust accordingly
  (the game's URLs are hard-coded though, so off-port HTTPS won't work).

- Listener gets nothing: hosts file entry not active. Try
       ipconfig /flushdns
  and re-check Resolve-DnsName.

- Listener gets HTTP requests but no HTTPS: cert handshake failing. RPCS3
  log will say "SSL_*" something; see the cert section above.
