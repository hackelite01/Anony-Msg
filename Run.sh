#!/usr/bin/env bash

clear
echo -e "\e[4;31m  HackElite !!! \e[0m"
echo -e "\e[1;32m Presents \e[0m"
echo -e "\e[1;34m"
figlet -f slant Anony-MSG
echo "Press Enter To Launch"
read a1
if [[ -s update.hackelite ]];then
echo "All Requirements Found...."
else
echo 'Installing Requirements....'
echo .
echo .
apt install figlet toilet python curl ruby -y
apt install python3-pip
gem install lolcat
echo This Tool Is Created By HackElite
echo This Script Is Created By HackElite Mayank>update.hackelite
echo Requirements Installed....
echo Press Enter To Continue...
read upd
fi
while :
do
rm *.xxx >/dev/null 2>&1
clear
echo -e "\e[1;31m"
figlet -f slant Anony-MSG | lolcat
echo -e "\e[1;34m Created By \e[1;32m"  #blue color
toilet -f mono12 -F border HE | lolcat
echo -e "\e[4;34mThis Tool Is Created By HackElite \e[0m" #underline+blue
echo -e "\e[1;34mFor Any Queries Mail Us!!!\e[0m"
echo -e "\e[1;32m        Mail: hackelite.sup@gmail.com \e[0m" #yellow
echo -e "\e[4;32mYouTube Page: https://youtube.com/channel/UCctC7DrSBTHcc8R-BrB9qRg \e[0m" #green
echo -e "\e[1;34m    Telegram: https://t.me/hackelite01 \e[0m"
echo " "
echo -e "\e[4;31mRead Instruction Carefully !!! \e[0m" #red
echo " "
echo "Enter 1 To  Run Anony-Msg "
echo "Enter 2 To  Track Anony-Msg "  #white
echo "Enter 3 To  Update "
echo "Enter 4 To  Check Features "
echo "Enter 5 To  Exit "
read ch
if [ $ch -eq 1 ];then
clear
echo -e "\e[1;32m"
rm *.xxx >/dev/null 2>&1
python3 send.py
rm *.xxx >/dev/null 2>&1
exit 0
elif [ $ch -eq 2 ];then
clear
echo -e "\e[1;32m"
echo 'Track Anony-Msg'> call.xxx
python3 send.py track
rm *.xxx >/dev/null 2>&1
exit 0
elif [ $ch -eq 3 ];then
clear
apt install git -y
echo -e "\e[1;34m Requesting Update From Source..."
echo -e "\e[1;34m Request Attempt Successful.."
echo -e "\e[1;34m Updating Now..."
git clone yhttps://github.com/hackelite01/Anony-Msg
if [[ -s Anony-Msg/Run.sh ]];then
cd Anony-Msg
cp -r -f * .. > temp
cd ..
rm -rf  Anony-Msg >> temp
rm update.hackelite >> temp
rm temp
chmod +x Run.sh
fi
echo -e "\e[1;32m Anony-Msg Will Refresh Now..."
echo -e "\e[1;32m All The Required Packages Have Been Installed..."
echo -e "\e[1;34m Press Enter To Refresh..."
read a6
./Run.sh
exit
elif [ $ch -eq 4 ];then
clear
echo -e "\e[1;33m"
figlet -f slant Anony-Msg | lolcat
echo -e "\e[1;34mCreated By \e[1;34m"  #Blue Color
toilet -f mono12 -F border HE | lolcat
echo  " "
echo -e "\e[1;32m                   Features\e[1;34m"
echo "  Ultra-Fast-Smooth Sending"
echo "  International Sending"
echo "  Tracking "
echo "  Automatic Future Updates"
echo "  Easy To Use And Embed in Code"
echo""
echo -e "\e[1;32m                   Contributors\e[1;33m"
echo -e "\e[1;33m      [*]  HackElite   \e[1;31m"
echo "         [-] Mail At: hackelite.sup@gmail.com"
echo -e "\e[1;33m      [*]  YouTube   \e[1;31m"
echo "         [-] Ping At: https://youtube.com/channel/UCctC7DrSBTHcc8R-BrB9qRg"
echo -e "\e[1;33m      [*]  Telegram   \e[1;31m"
echo "         [-] Ping At: https://t.me/hackelite01/"
echo ""
echo ""
echo -e "\e[1;31m This is Only For Educational Purposes or To Prank.\e[0m"
echo -e "\e[1;31m Do not Use This To Irritate Others. \e[0m"
echo -e "\e[1;31m Do not Use This To Harm Others. \e[0m"
echo -e "\e[1;31m We Are Not Responsible For The Misuse Of The Script. \e[0m"
echo -e "\e[1;32m Update If Doesn't Work.\e[0m"
echo  " "
echo -e "\e[4;31m That's All !!!\e[0m"
echo -e "\e[1;34m For Any Queries Mail Us!!!\e[0m"
echo -e "\e[1;32m        Mail: hackelite.sup@gmail.com \e[0m"
echo -e "\e[4;32mYouTube Page: https://youtube.com/channel/UCctC7DrSBTHcc8R-BrB9qRg \e[0m"
echo "Press Enter To Return To Main Menu"
read a3
clear
elif [ $ch -eq 5 ];then
clear
echo -e "\e[1;31m"
figlet -f slant Anon-SMS | lolcat
echo -e "\e[1;34m Created By \e[1;32m"
toilet -f pagga -F border HackElite | lolcat
echo -e "\e[1;34m For Any Queries Mail Us!!!\e[0m"
echo -e "\e[1;32m        Mail: hackelite.sup@gmail.com \e[0m"
echo -e "\e[1;32m    Telegram: https://t.me/hackelite01 \e[0m"
echo -e "\e[4;32mYouTube Page: https://youtube.com/channel/UCctC7DrSBTHcc8R-BrB9qRg \e[0m"
echo " "
exit 0
else
echo -e "\e[4;32m Invalid Input Detected !!! \e[0m"
echo "Press Enter To Return To Main Menu"
read a3
clear
fi
done
