#! /usr/bin/env bash

if [[ "$1" == "" ]]; then
    echo "Usage: $0 <ttynumber> <builddir>"
fi

baudrate=57600
avrdude -v -p atmega32u4 -c avr109 -P /dev/ttyACM$1 -b $baudrate -D -Uflash:w:$2/icgcamac-switch-test.ino.hex:i
avrdude -v -p atmega32u4 -c avr109 -P /dev/ttyACM$1 -D -Uflash:w:$2/icgcamac-switch-test.ino.hex:i || ls /dev/ttyACM*

