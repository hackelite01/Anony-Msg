#!/usr/bin/env python
import threading
import string
import base64
import urllib.request
import urllib.parse
import os
import time
import sys
import random

try:
    import requests
except ImportError:
    print('Error !! : Some dependencies are not installed')
    print('Error Occured!!!\nUse Non-Supported Version')
    input('Press Any Key To Use Non-supported Version')
    os.system('bash send.sh --sendsms')

# colors
yellow='\033[93m'
gren='\033[92m'
cyan='\033[96m'
pink='\033[95m'
red='\033[91m'
b='\033[1m'
W = '\033[0m'
colors = ['\033[1;31m', '\033[1;32m', '\033[1;33m', '\033[1;34m', '\033[1;35m', '\033[1;36m']

# The Credit For This Code Goes To  https://github.com/hackelite01/
# And The Contributors Mentioned At Mayank https://github.com/hackelite01/Anony-Msg
# If You Wanna Take Credits, Please Look Yourself Again!!

def clr():
	if os.name == 'nt':
		os.system('cls')
	else:
		os.system('clear')

def banner():
    clr()
    logo = """                                                  
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
█░▄▄▀█░▄▄▀█▀▄▄▀█░▄▄▀█░██░█████░▄▀▄░█░▄▄█░▄▄▄
█░▀▀░█░██░█░██░█░██░█░▀▀░█▄▄██░█░█░█▄▄▀█░█▄▀
█░██░█▄██▄██▄▄██▄██▄█▀▀▀▄█████░███░█▄▄▄█▄▄▄▄
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀          """
    print(logo)
    print("\n")
    

def Track() :
  TXTID = input("Enter Text ID of Anon-SMS \n\t -->>")
  os.system(f"curl https://textbelt.com/status/{TXTID}")
  input("\nPress Enter To Exit..")
  print("\nThanks For Using Anony-Msg..")
  print("\tWe Hope To See You Again\n Type bash Run.sh\n\tTo Run Again..")
  input('\n\n\nThank You For Using Anony-Msg\nAfter v1.45 There are Ads Enabled in this Tool.\nPress Enter To Continue to View Our Channel.\n\Join The Channel And Share Plz')
  os.system('xdg-open https://t.me/hackelite01')
  print("Thank you for Your Time...")
  exit()

def update():
    stuff_to_update = ['send.py', 'Run.sh', '.version']
    for fl in stuff_to_update:
        dat = urllib.request.urlopen("https://raw.githubusercontent.com/hackelite01/Anony-Msg/master/" + fl).read()
        file = open(fl, 'wb')
        file.write(dat)
        file.close()
    print('\n\t\tUpdated Successfull !!!!')
    print('\tRun The Script Again...')
    exit()

clr()
banner()
try:
    urllib.request.urlopen('https://www.google.com')
except Exception:
    print("Error While Connecting To Internet!!!")
    print("\tPlease Connect To Internet To Continue...\n")
    input('Exiting....\n Press Enter To Exit....')
    exit()
print('\tChecking For Updates...')
ver = urllib.request.urlopen("https://raw.githubusercontent.com/hackelite01/Anony-Msg/master/.version").read().decode('utf-8')
verl = ''
try:
    verl = open(".version", 'r').read()
except Exception:
    pass
if ver != verl:
    print('\n\t\tAn Update is Available....')
    print('\tUpdating Anony-Msg...')
    update()
print("Congratulation")
print("Your Version is Up-To-Date")
print('\n\tStarting Anony-Msg...\n')
try:
    noti = urllib.request.urlopen("https://raw.githubusercontent.com/hackelite01/Anony-Msg/master/.notify").read().decode('utf-8')
    if len(noti) > 10:
        print('\nNotification : ' + noti + '\n')
except Exception:
    pass
    
    
    
while True:
	print("\033[0mThis Tool Is Used To Send Anonymous Messages")
	break
type = 0
try:
	if sys.argv[1] == "track":
		type = 1
except Exception:
	type = 0
if type == 1:
	print("Track The Anonymous Message You Sent Using This Tool.")
	print()
	Track()
elif type == 0:
	while True:
		print("Enter The Details Of The Person You Want To Send Anonymous Message")
		cc = input("\tEnter Country Code (Without +) : ")
		if '+' in cc:
		        tc = list(cc)
		        tc.remove('+')
		        cc = ''.join(tc)
		        cc = cc.strip()
		if len(cc) >= 4 or len(cc) < 1:
		        print('\n\nInvalid Country Code..\n\t\tCountry Codes Are Generally 1-3 digits...\n')
		        continue
		pn = input("Enter Phone Number : +" + cc + " ")
		if len(pn) <= 6:
		        print('\n\nInvalid Phone Number..\n')
		        continue
		numbe = cc + pn
		if not numbe.isdigit():
		            print('\n\nPhone Number Must Consist Of Numbers Only\n')
		            continue
		receiver = '+' + numbe
		text = input("Enter Message to send : ")
		
		resp = requests.post('https://textbelt.com/text',{
			'phone' : receiver,
			'message' : text ,
			'key' : 'textbelt'
		})
		
		print(resp.json())
		input('\n\n\nThank You For Using Anony-Msg\nAfter v1.45 There are Ads Enabled in this Tool.\nPress Enter To Continue to VieW Our Channel.\n\Join The Channel And Share Plz.')
		os.system('xdg-open https://t.me/hackelite01')
		print("Thank you for Your Time...")
		break
		if '"success" : true ' in resp.text:
		    print("\033[92m Message Sent Succesfully \033[0m")
		    input('\n\t\tPress Enter To Exit...')
		    banner()
		    exit()
		if '"success" : false ' in resp.text:
		    print("\033[91m Error Occured")
		    print("\033[91m Failed to send SMS! ")
		    input('\n\t\tPress Enter To Exit...')
		    banner()
		    exit()
		exit() 
