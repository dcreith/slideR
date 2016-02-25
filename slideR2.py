# Slide timelapse controller for Raspberry Pi
# This must run as root (sudo python slideR.py) due to framebuffer, etc.
#
# http://www.adafruit.com/products/998  (Raspberry Pi Model B)
# http://www.adafruit.com/products/1601 (PiTFT Mini Kit)
#
# Prerequisite tutorials: aside from the basic Raspbian setup and PiTFT setup
# http://learn.adafruit.com/adafruit-pitft-28-inch-resistive-touchscreen-display-raspberry-pi
#
# slideR.py by Dave Creith (dave@creith.net)
#
# based on lapse.py by David Hunt (dave@davidhunt.ie)
# based on cam.py by Phil Burgess / Paint Your Dragon for Adafruit Industries.
# BSD license, all text above must be included in any redistribution.

import wiringpi2
import atexit
import cPickle as pickle
import errno
import fnmatch
import io
import os
import pygame
import threading
import signal
import sys
import time

from pygame.locals import *
from subprocess import call
from datetime import datetime, timedelta

# UI classes ---------------------------------------------------------------

# Icon is a very simple bitmap class, just associates a name and a pygame
# image (PNG loaded from icons directory) for each.
# There isn't a globally-declared fixed list of Icons.  Instead, the list
# is populated at runtime from the contents of the 'icons' directory.

class Icon:

    def __init__(self, name):
      self.name = name
      try:
        self.bitmap = pygame.image.load(iconPath + '/' + name + '.png')
      except:
        pass

# Button is a simple tappable screen region.  Each has:
#  - bounding rect ((X,Y,W,H) in pixels)
#  - optional background color and/or Icon (or None), always centered
#  - optional foreground Icon, always centered
#  - optional single callback function
#  - optional single value passed to callback
# Occasionally Buttons are used as a convenience for positioning Icons
# but the taps are ignored.  Stacking order is important; when Buttons
# overlap, lowest/first Button in list takes precedence when processing
# input, and highest/last Button is drawn atop prior Button(s).  This is
# used, for example, to center an Icon by creating a passive Button the
# width of the full screen, but with other buttons left or right that
# may take input precedence (e.g. the Effect labels & buttons).
# After Icons are loaded at runtime, a pass is made through the global
# buttons[] list to assign the Icon objects (from names) to each Button.

class Button:

    def __init__(self, rect, **kwargs):
      self.rect     = rect # Bounds
      self.color    = None # Background fill color, if any
      self.iconBg   = None # Background Icon (atop color fill)
      self.iconFg   = None # Foreground Icon (atop background)
      self.bg       = None # Background Icon name
      self.fg       = None # Foreground Icon name
      self.callback = None # Callback function
      self.value    = None # Value passed to callback
      for key, value in kwargs.iteritems():
        if   key == 'color': self.color    = value
        elif key == 'bg'   : self.bg       = value
        elif key == 'fg'   : self.fg       = value
        elif key == 'cb'   : self.callback = value
        elif key == 'value': self.value    = value

    def selected(self, pos):
      x1 = self.rect[0]
      y1 = self.rect[1]
      x2 = x1 + self.rect[2] - 1
      y2 = y1 + self.rect[3] - 1
      if ((pos[0] >= x1) and (pos[0] <= x2) and
          (pos[1] >= y1) and (pos[1] <= y2)):
        if self.callback:
          if self.value is None: self.callback()
          else:                  self.callback(self.value)
        return True
      return False

    def draw(self, screen):
      if self.color:
        screen.fill(self.color, self.rect)
      if self.iconBg:
        screen.blit(self.iconBg.bitmap,
          (self.rect[0]+(self.rect[2]-self.iconBg.bitmap.get_width())/2,
           self.rect[1]+(self.rect[3]-self.iconBg.bitmap.get_height())/2))
      if self.iconFg:
        screen.blit(self.iconFg.bitmap,
          (self.rect[0]+(self.rect[2]-self.iconFg.bitmap.get_width())/2,
           self.rect[1]+(self.rect[3]-self.iconFg.bitmap.get_height())/2))

    def setBg(self, name):
      if name is None:
        self.iconBg = None
      else:
        for i in icons:
          if name == i.name:
            self.iconBg = i
            break

# UI callbacks -------------------------------------------------------------
# These are defined before globals because they're referenced by items in
# the global buttons[] list.

def backlightCallback(n):         # toggle the screen backlight on and off
    global backlightState
    if backlightState==0:
        backlightState=1
        os.system("echo '1' > /sys/class/gpio/gpio252/value")
    else:
        backlightState=0
        os.system("echo '0' > /sys/class/gpio/gpio252/value")

def gpioCleanup(n):
    print 'GPIO Clean up'
    gpio.digitalWrite(Pins['Shutter'],gpio.LOW)
    gpio.digitalWrite(Pins['Focus'],gpio.LOW)
    gpio.digitalWrite(Pins['LedR'],gpio.LOW)
    gpio.digitalWrite(Pins['LedG'],gpio.LOW)
    gpio.digitalWrite(Pins['LedB'],gpio.LOW)
    gpio.digitalWrite(Pins['MotorA1'],gpio.LOW)
    gpio.digitalWrite(Pins['MotorA2'],gpio.LOW)
    gpio.digitalWrite(Pins['MotorB1'],gpio.LOW)
    gpio.digitalWrite(Pins['MotorB2'],gpio.LOW)

    gpio.pinMode(Pins['Shutter'],gpio.INPUT)
    gpio.pinMode(Pins['Focus'],gpio.INPUT)
    gpio.pinMode(Pins['LedR'],gpio.INPUT)
    gpio.pinMode(Pins['LedG'],gpio.INPUT)
    gpio.pinMode(Pins['LedB'],gpio.INPUT)
    gpio.pinMode(Pins['MotorA1'],gpio.INPUT)
    gpio.pinMode(Pins['MotorA2'],gpio.INPUT)
    gpio.pinMode(Pins['MotorB1'],gpio.INPUT)
    gpio.pinMode(Pins['MotorB2'],gpio.INPUT)

def shutdownPi(n):                # return to previous screen or shutdown Pi
    global screenMode
    if n==-1:
        screenMode = 1
    elif n==1:
        screen.blit(img,
            ((320 - img.get_width() ) / 2,
            (240 - img.get_height()) / 2))
                
        myfont = pygame.font.SysFont('Arial', smallfont)
        myfont.set_bold(False)
        msgString = 'Turn Power Off In 15 Seconds'
        label = myfont.render(msgString, 1, (fontcolour))
        screen.blit(label, (xPos(msgString,0,screenMode,myfont), 90))
        pygame.display.update()
        saveBasic()
        saveState('shutdownPi')
        time.sleep(5)
        gpioCleanup

	# shutdown the Raspberry Pi
    #   sys.exit()
    	os.system("sudo shutdown -h now")
        time.sleep(10)

def left(delay, steps):        # drive motor forwards a number of steps
  global forwardSeq
  for i in range(steps):
    for step in forwardSeq:
      stepMotor(step)
      time.sleep(delay)
  return (i + 1)
 
def right(delay, steps):       # drive motor backwards a number of steps
  global reverseSeq
  for i in range(steps):
    for step in reverseSeq:
      stepMotor(step)
      time.sleep(delay)
  return ((i + 1) * -1)

def stepMotor(step):           # drive motor
  global Pins
  gpio.digitalWrite(Pins['MotorA1'], step[0] == '1')
  gpio.digitalWrite(Pins['MotorA2'], step[1] == '1')
  gpio.digitalWrite(Pins['MotorB1'], step[2] == '1')
  gpio.digitalWrite(Pins['MotorB2'], step[3] == '1')

def travelRail(delay, steps):  # stop on 0 steps
    global slideState
    if steps > 0:
        slideState['Sliding'] = True
        if slideState['DirectionLeft']:
            stepsTaken = left(delay, steps)
        else:
            stepsTaken = right(delay, steps)
        slideState['Sliding'] = False
        slideState['Position'] = slideState['Position'] + stepsTaken
    else:
        slideState['Sliding'] = False
        stepMotor('0000')
    
    saveState('travelrail')

def positionCallback(n):          # set the slide end positions
    global slideBasic
    global slideState
    global dictIdx
    global numberstring
    global screenMode
    global returnScreen
    
    if n == 1:                    # set left slide position for program
        slideState['Left'] = slideState['Position']
    elif n == 2:                  # set right slide position for program
        slideState['Right'] = slideState['Position']
    elif n == 3:                  # set the left step point directly
        dictIdx='Left'
        numberstring = str(slideState[dictIdx])
        screenMode = 2
        returnScreen = 4
    elif n == 4:                  # set the right step point directly
        dictIdx='Right'
        numberstring = str(slideState[dictIdx])
        screenMode = 2
        returnScreen = 4
    elif n == 5:                  # set left maximum slide position
        slideBasic['MaxLeft'] = slideState['Position']
    elif n == 6:                  # set right maximum slide position
        slideBasic['MaxRight'] = 0
        slideState['Position'] = 0
    elif n == 7:                  # set the max left end point directly
        dictIdx='MaxLeft'
        numberstring = str(slideBasic[dictIdx])
        screenMode = 2
        returnScreen = 6
    elif n == 8:                  # set the max right end point directly
        dictIdx='MaxRight'
        numberstring = str(slideBasic[dictIdx])
        screenMode = 2
        returnScreen = 6
        
    saveBasic()
    saveState('positionCallback')

def rotationCallback(n):          # set the rotation positions
    global slideBasic
    global slideState

def slideCallback(n):             # set the slide motor direction and run the motor
    global slideBasic
    global slideState
    global siStart
    global siStop
    siStart = 0
    siStop = 1
    # 1 is left
    # 2 is right
    # 3 is left end (program end)
    # 4 is right end (program end)
    # 0 is change set direction
    # change motor direction
    # if motor is running then shut it off
    # if motor is not running then start it
    if n == 0:
        if slideState['Sliding']:
            travelRail(slideBasic['MinDelay'],0)
        if slideState['DirectionLeft']:
            slideState['DirectionLeft'] = False
        else:
            slideState['DirectionLeft'] = True
    elif n == 1:
        slideState['DirectionLeft'] = True
        if slideState['Sliding']:
            travelRail(slideBasic['MinDelay'],0)
        else:
            travelRail(slideBasic['MinDelay'],1)
    elif n == 2:
        slideState['DirectionLeft'] = False
        if slideState['Sliding']:
            travelRail(slideBasic['MinDelay'],0)
        else:
            travelRail(slideBasic['MinDelay'],1)
    elif n == 3:
        slideMovement = slideState['Left'] - slideState['Position']
        if slideMovement > 0:
            slideState['DirectionLeft'] = True
        else:
            slideState['DirectionLeft'] = False
        if slideState['Sliding']:
            travelRail(slideBasic['MinDelay'],0)
        else:
            travelRail(slideBasic['MinDelay'],abs(slideMovement))
    elif n == 4:
        slideMovement = slideState['Position'] - slideState['Right']
        if slideMovement < 0:
            slideState['DirectionLeft'] = True
        else:
            slideState['DirectionLeft'] = False
        if slideState['Sliding']:
            travelRail(slideBasic['MinDelay'],0)
        else:
            travelRail(slideBasic['MinDelay'],abs(slideMovement))
    elif n == 5:
        slideMovement = slideBasic['MaxLeft'] - slideState['Position']
        if slideMovement > 0:
            slideState['DirectionLeft'] = True
        else:
            slideState['DirectionLeft'] = False
        if slideState['Sliding']:
            travelRail(slideBasic['MinDelay'],0)
        else:
            travelRail(slideBasic['MinDelay'],abs(slideMovement))
    elif n == 6:
        slideMovement = slideState['Position'] - slideBasic['MaxRight']
        if slideMovement < 0:
            slideState['DirectionLeft'] = True
        else:
            slideState['DirectionLeft'] = False
        if slideState['Sliding']:
            travelRail(slideBasic['MinDelay'],0)
        else:
            travelRail(slideBasic['MinDelay'],abs(slideMovement))

def numericCallback(n):           # keypad capture
    global screenMode
    global returnScreen
    global numberstring
    global slideState
    global slideBasic
    global dictIdx

    if n < 10:                    # capture keystroke to value (0-9)
        numberstring = numberstring + str(n)
    elif n == 10:                 # clear value
        numberstring = numberstring[:-1*(len(numberstring))]
    elif n == 11:                 # cancel update
        screenMode = returnScreen
    elif n == 12:                 # return value as int
        screenMode = returnScreen
        if numberstring:
            numeric = int(numberstring)
            if screenMode == 1:
                slideState[dictIdx] = numeric
            elif screenMode == 4:
                slideState[dictIdx] = numeric
            elif screenMode == 6:
                slideBasic[dictIdx] = numeric
    elif n == 13:                 # return value as float (shutter & settling values)
        screenMode = returnScreen
        if len(numberstring) > 0:
            numeric = float(numberstring)
            slideState[dictIdx] = numeric
    elif n == 14:                 # return value as fraction of second (float)
        screenMode = returnScreen
        if len(numberstring) > 0:
            numeric = 1 / float(numberstring)
            slideState[dictIdx] = numeric

def programCallback(n):            # select a parameter and goto keypad (-1 returns to screen 0)
    global screenMode
    global slideState

    if n == -1:
        screenMode = 1
        saveState('programCallback')
        reasonableValues()
        if timelapseSettings():         # Calculate timelapse execution values
            taskIndicator = 'stopped'

    if n == 0:
        screenMode = 1
        reasonableValues()
        if timelapseSettings():         # Calculate timelapse execution values
            taskIndicator = 'stopped'


def valuesCallback(n):            # select a parameter and goto keypad (-1 returns to screen 0)
    global screenMode
    global returnScreen
    global numberstring
    global numeric
    global slideState
    global dictIdx

    if n == -1:
        screenMode = 0
        saveState('valuesCallback')
        reasonableValues()
        if timelapseSettings():         # Calculate timelapse execution values
            taskIndicator = 'stopped'

    if n == 0:
        screenMode = 0
        reasonableValues()
        if timelapseSettings():         # Calculate timelapse execution values
            taskIndicator = 'stopped'

    if n == 1:
        dictIdx='Shutter'
        # set the source icon here
        sValue = float(slideState[dictIdx])
        if (sValue < 1):
            numberstring = str(int(1 / sValue))
        else:
            numberstring = str(int(slideState[dictIdx]))
        screenMode = 3
        returnScreen = 1
    elif n == 2:
        dictIdx='Timespan'
        numberstring = str(slideState[dictIdx])
        screenMode = 2
        returnScreen = 1
    elif n == 3:
        dictIdx='Images'
        numberstring = str(slideState[dictIdx])
        screenMode = 2
        returnScreen = 1
    elif n == 5:
        dictIdx='Settle'
        sValue = float(slideState[dictIdx])
        if (sValue < 1):
            numberstring = str(int(1 / sValue))
        else:
            numberstring = str(int(slideState[dictIdx]))
        screenMode = 3
        returnScreen = 1

def viewCallback(n):              # Set branch to screen
    global screenMode
    global siStart
    global siStop
    siStart = 0
    siStop = 1

    if n is 1:                    # Gear icon - branch to parameters
      screenMode = 1
    elif n is 4:                  # Travel rail - branch to rail parameters
      screenMode = 4
    elif n is 5:                  # Rotate camera - branch to rotate parameters
      screenMode = 5
    elif n is 6:                  # Maximum rail parameters
      screenMode = 6
    elif n is 7:                  # Save, clear and select program
      screenMode = 7
    elif n is 8:                  # Shutdown Pi - branch to shutdown screen
      screenMode = 8

def startCallback(n):             # start/Stop the timelapse thread
    # threadExited - initiated as False
    #              - set to True in timelapse when image count exhausted
    #              - set to False here when starting a thread
    # busy - initiated as False
    #      - set to False here when explicitly ending the thread with keypad 'Stop'
    #      - set to True at start of timelapse, False at completion
    global t, busy, threadExited
    global slideState
    global startTime
    global doneNotify
    global siStart
    global siStop
    siStart = 0
    siStop = 1

    midPoint = (((slideState['Left'] - slideState['Right']) / 2) + slideState['Right'])
    # if right of middle then start at the right, if left of middle start at left
    if ((slideState['Position'] <= midPoint) &
        (slideState['Position'] != slideState['Right'])):
        slideCallback(4)
    if ((slideState['Position'] > midPoint) &
        (slideState['Position'] != slideState['Left'])):
        slideCallback(3)
    if (slideState['Position']) == slideState['Right']:
        slideState['DirectionLeft'] = True
    else:
        slideState['DirectionLeft'] = False
    saveState('startCallback 1')
    
    if n == 1:
    #    print 'setLED 4'
        setLED('running')
        if busy == False:
            if (threadExited == True):
                # Re-instanciate the object for the next start
                t = threading.Thread(target=timeLapse)
                threadExited = False
            t.start()
#            startTime = time.time()
    if n == 0:
        if busy == True:
            busy = False
            t.join()
            slideState['CurrentImage'] = 0
            slideState['State'] = 0
            saveState('startCallback 2')
            taskIndicator  = 'done'
            startTime = 0.0
            doneNotify = time.time() + (30 * 1) # set done LED show delay
            # Re-instanciate the object for the next time around.
            t = threading.Thread(target=timeLapse)

def setspeedCallback(n):           # determine the step time
    # 0 or cancel is irrelevant for this screen as changes are already loaded
    global screenMode
    global returnScreen
    global numberstring
    global numeric
    global slideState
    global dictIdx

    if n == -1:
        screenMode = 4
        saveBasic()
        saveState('setspeedCallback')
        reasonableValues()
        if timelapseSettings():         # Calculate timelapse execution values
            taskIndicator = 'stopped'

    if n == 0:
        screenMode = 4
        reasonableValues()
        if timelapseSettings():         # Calculate timelapse execution values
            taskIndicator = 'stopped'
    
    if n == 1:                     # run for slideBasic['Steps'] and find time
        if slideBasic['Steps'] < (slideBasic['MaxLeft'] - slideState['Position']):
            slideState['DirectionLeft'] = 1
        elif slideBasic['Steps'] > slideState['Position']:
            slideState['DirectionLeft'] = 0
        else:
            slideBasic['Steps'] = slideState['Position']
            slideState['DirectionLeft'] = 0
        startTime = time.time()
        travelRail(slideBasic['MinDelay'], slideBasic['Steps'])
        endTime = time.time()
        elapsedTime = abs(endTime - startTime)
        
        sec = timedelta(seconds=int(elapsedTime))
        d = datetime(1,1,1) + sec
        if d.hour > 0:
            labeltext = '%dh%dm%ds' % (d.hour, d.minute, d.second)
        else:
            labeltext = '%dm%ds' % (d.minute, d.second)
        if elapsedTime == 0: elapsedTime = 0.1

        slideBasic['StepTime'] = (elapsedTime / slideBasic['Steps'])
        returnScreen = 6

    elif n == 3:
        dictIdx='Steps'
        numberstring = str(slideBasic[dictIdx])
        screenMode = 2
        returnScreen = 6

def programCallback(n):           # set, save and clear programs
   Button((  0,  0, 64, 64), bg='P1',          cb=programCallback, value=1),
   Button(( 64,  0, 64, 64), bg='P2',          cb=programCallback, value=2),
   Button((128,  0, 64, 64), bg='P3',          cb=programCallback, value=3),
   Button((192,  0, 64, 64), bg='P4',          cb=programCallback, value=4),
   Button((256,  0, 64, 64), bg='Current',     cb=programCallback, value=5),
   Button((  0,180,100, 60), bg='smallCancel', cb=programCallback, value=0),
   Button((100,180, 60, 60), bg='clr',         cb=programCallback, value=6),
   Button((160,180, 60, 60), bg='save',        cb=programCallback, value=7),
   Button((220,180,100, 60), bg='use',         cb=programCallback, value=-1)],
   
    #dictlist = [dict() for x in range(n)]
    programs = [pgm{} for x in range(4)]

    global screenMode
    global returnScreen
    global numberstring
    global numeric
    global slideState
    global dictIdx
    global selectedProgram
    
    if n == -1:         # use the shown values
        screenMode = 0
        reasonableValues()
        if timelapseSettings():
            taskIndicator = 'stopped'

    if n == 0:          # return
        screenMode = 0
            
    if n == 1:          # show program selected
        selectedProgram = 1
        showProgram = program(n)
    elif n == 2:
        selectedProgram = 2
        showProgram = program(n)
    elif n == 3:
        selectedProgram = 3
        showProgram = program(n)
    elif n == 4:
        selectedProgram = 4
        showProgram = program(n)
    elif n == 5:
        selectedProgram = 5
        showProgram = program(n)
    elif n == 6:        # clear selected
    elif n == 7:        # save current to selected
        program(selectedProgram) = slideState

def timeLapse():                  # execute the timelapse (separate thread)
    global busy, threadExited
    # threadExited - initiated as False
    #              - set to True here when image count exhausted
    #              - set to False in on 'Start' from keypad when starting a thread
    # busy - initiated as False
    #      - set to False when explicitly ending the thread with keypad 'Stop'
    #      - set to True here at start, False at completion
    #      - breaks loop when False
    global slideState
    global slideBasic
    global Pins
    
    global settlingTime
    global shutterTime
    global focusPause

    global taskIndicator
    global doneNotify
    global startTime

    # set copies of the following to isolate operation from setup
    # images, motorpin
    # travelpulse, focusPause, shutterTime
    
    busy = True
    
    slideState['State'] = 1
    
    startTime = time.time()
    
    for i in range( 1 , slideState['Images'] + 1 ):
        if busy == False:
            break

        # move slide forward on all but first image
        if i!=1:
            taskIndicator = 'travel'
            travelRail(slideState['Delay'], slideState['PulseSteps'])
    
        taskIndicator = 'settling'
        time.sleep(settlingTime)

        taskIndicator = 'fire'
        # trigger the focus
        gpio.digitalWrite(Pins['Focus'],gpio.HIGH)
        time.sleep(focusPause)

        # trigger the shutter
        gpio.digitalWrite(Pins['Shutter'],gpio.HIGH)
        time.sleep(shutterTime)
        gpio.digitalWrite(Pins['Shutter'],gpio.LOW)
        gpio.digitalWrite(Pins['Focus'],gpio.LOW)

        slideState['CurrentImage'] = i
#        startTime = time.time()
        saveState('timelapse loop')
    
    slideState['CurrentImage'] = 0
    slideState['State'] = 0
    saveState('timelapse done')
    
    doneNotify = time.time() + (30 * 1)  # set done LED show delay
    
    taskIndicator  = 'done'
    busy = False
    threadExited = True

def is_integer(s):
    try:
        int(s)
        return True
    except ValueError:
        return False

def is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def reasonableValues():
    global slideState
    global slideBasic
    global focusPause

    if not is_float(slideBasic['StepTime']):   slideBasic['StepTime'] = 0.020
    if not is_float(slideBasic['MinDelay']):   slideBasic['MinDelay'] = 0.002
    if not is_integer(slideBasic['MaxLeft']):  slideBasic['MaxLeft'] = 200
    if not is_integer(slideBasic['MaxRight']): slideBasic['MaxRight'] = 0
    if not is_integer(slideBasic['MaxCCW']):   slideBasic['MaxCCW'] = 100
    if not is_integer(slideBasic['MaxCW']):    slideBasic['MaxCW'] = 0
    if not is_integer(slideBasic['Steps']):    slideBasic['Steps'] = 10

    # Min 20 milliseconds
    # Max 1 day
    if    slideBasic['StepTime']<0.010: slideBasic['StepTime'] = 0.010
    elif  slideBasic['StepTime']>0.5: slideBasic['StepTime'] = 0.010

    if    slideBasic['MinDelay']<0.002: slideBasic['MinDelay'] = 0.002
    elif  slideBasic['MinDelay']>86400: slideBasic['MinDelay'] = 0.002

    # Motorsteps
    if slideBasic['MaxLeft'] > 5000:
        slideBasic['MaxLeft'] = 5000
    if slideBasic['MaxRight'] < 1:
        slideBasic['MaxRight'] = 0

    # Motorsteps
    if slideBasic['Steps'] > int((slideBasic['MaxLeft'] / 2) + 1):
        slideBasic['Steps'] = int((slideBasic['MaxLeft'] / 2) + 1)
    if slideBasic['Steps'] < 1:
        slideBasic['Steps'] = int((slideBasic['MaxLeft'] / 2) + 1)

    if not is_float(slideState['Shutter']):   slideState['Shutter'] = 1 / 60
    if not is_float(slideState['Settle']):    slideState['Settle'] = 0
    if not is_float(slideState['Delay']):     slideState['Delay'] = 0.020
    
    if not is_integer(slideState['Images']):      slideState['Images'] = 60
    if not is_integer(slideState['Timespan']):    slideState['Timespan'] = 30
    if not is_integer(slideState['ShootTime']):   slideState['ShootTime'] = 10
    if not is_integer(slideState['Left']):        slideState['Left'] = 100
    if not is_integer(slideState['Right']):       slideState['Right'] = 10
    if not is_integer(slideState['CCW']):         slideState['CCW'] = 100
    if not is_integer(slideState['CW']):          slideState['CW'] = 0
    if not is_integer(slideState['PulseSteps']):  slideState['PulseSteps'] = 2
    if not is_integer(slideState['Position']):    slideState['Position'] = 2
    if not is_integer(slideState['Rotation']):    slideState['Rotation'] = 2
    if not is_integer(slideState['CurrentImage']):slideState['CurrentImage'] = 0

    # Min 1/8000s
    # Max 90 seconds
    if    slideState['Shutter']==0: slideState['Shutter'] = 1 / 60
    elif  slideState['Shutter']<(1/8000): slideState['Shutter'] = 1 / 60
    elif  slideState['Shutter']>90: slideState['Shutter'] = 1 / 60

    # Min 1 image
    # Max 500 images
    if slideState['Images']==0 or slideState['Images'] > 500:
        slideState['Images'] = 10

    # Min 1 minute
    # Max 24 hours
    if    slideState['Timespan']<(1): slideState['Timespan'] = 30
    elif  slideState['Timespan']>1440: slideState['Timespan'] = 60

    # Min 20 milliseconds
    # Max 1 day
    if    slideState['Delay']<0.002: slideState['Delay'] = 0.002
    elif  slideState['Delay']>86400: slideState['Delay'] = 0.002
        
    if slideState['Left'] > slideBasic['MaxLeft']:
        slideState['Left'] = slideBasic['MaxLeft']
    if slideState['Right'] < slideBasic['MaxRight']:
        slideState['Right'] = slideBasic['MaxRight']

    if slideState['Position'] > slideBasic['MaxLeft']:
        slideState['Position'] = slideBasic['MaxLeft']
    if slideState['Position'] < slideBasic['MaxRight']:
        slideState['Position'] = slideBasic['MaxRight']

    # Motorsteps
    if   slideState['PulseSteps'] < 1: slideState['PulseSteps'] = 1
    elif slideState['PulseSteps'] > slideState['Left']:
          slideState['PulseSteps'] = 1

def timelapseSettings():
    global slideState
    global slideBasic
    global settlingTime
    global shutterTime
    global focusPause

    #debug
    #debugState('befoe timelapseSettings')
    #debugBasic('before timelapseSettings')
    #debug

    intervals = int(slideState['Images']) - 1
    if intervals < 1:
        intervals = 1

    # calc frame time
    settlingTime = float(slideState['Settle'])                              # time to wait before firing shutter
    shutterTime = float(slideState['Shutter'])                              # shutter speed
    frameTime = (shutterTime + settlingTime + focusPause) 		            # time for 1 image in seconds
    slideState['ShootTime'] = frameTime * int(slideState['Images'])         # time for all images in seconds
        
    totalSteps = slideState['Left'] - slideState['Right']
    if totalSteps == 0: totalSteps = 1
    slideState['PulseSteps'] = int(totalSteps / intervals)
    pulseTime = slideState['PulseSteps'] * slideBasic['StepTime']
    motorTime = pulseTime * intervals
    runTime = slideState['ShootTime'] + motorTime
    extraTime = (slideState['Timespan'] * 60) - runTime
    factoredSteps = totalSteps * stepFactor
    slideState['Delay'] = slideBasic['MinDelay']

    errFound = False
    if extraTime < 0:
        errFound = True
        sec = timedelta(seconds=int(runTime / 1000))
        d = datetime(1,1,1) + sec
        if d.hour > 0:
            errmsg = 'Min timespan of %dh%dm%ds' % (d.hour, d.minute, d.second)
        else:
            errmsg = 'Min timespan of %dm%ds' % (d.minute, d.second)
    else:
        slideState['Delay'] = slideState['Delay'] + round(extraTime / factoredSteps,3)

    #debug
    #debugState('after timelapseSettings')
    #debugBasic('after timelapseSettings')
    #debug
    #print "settlingTime...." + str(settlingTime)
    #print "shutterTime....." + str(shutterTime)
    #print "focusPause......" + str(focusPause)
    
    #print "frameTime......." + str(frameTime)
    
    #print "totalSteps......" + str(totalSteps)
    #print "motorTime......." + str(motorTime)
    #print "runTime........." + str(runTime)
    #print "extraTime......." + str(extraTime)
    #debug
    
    return errFound
            
def xPos(lbl,j,s,mf):         # determine starting x co-ordinate to place text
    # lbl->text, j->justification, s->screen, mf->font
    labelwidth = mf.size(lbl)[0]
    l = [5,65,5,5,65,65,65,5]             # leftmost co-ordinates for screens 0->7
    r = [320,260,320,320,260,260,260,320] # rightmost co-ordinates for screens 0->7
    if j==-1:
        x = l[s]
    elif j==1:
        x = r[s] - (labelwidth + 5)
        if x < 0:
            x = 160
    else:
        x = int((320 - labelwidth) / 2)
        if x < 0:
            x = 0
    return x

def setLED(a):
    global lastRGB
    #print 'setLED.....' + str(lastRGB) + ' a..' + str(a)
    if a!=lastRGB:
        lastRGB = a
        if a=='start':       # white
            gpio.digitalWrite(Pins['LedR'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedG'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedB'],gpio.HIGH)
        elif a=='ready':     # blue
            gpio.digitalWrite(Pins['LedR'],gpio.LOW)
            gpio.digitalWrite(Pins['LedG'],gpio.LOW)
            gpio.digitalWrite(Pins['LedB'],gpio.HIGH)
        elif a=='running':   # green
            gpio.digitalWrite(Pins['LedR'],gpio.LOW)
            gpio.digitalWrite(Pins['LedG'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedB'],gpio.LOW)
        elif a=='done':      # red
            gpio.digitalWrite(Pins['LedR'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedG'],gpio.LOW)
            gpio.digitalWrite(Pins['LedB'],gpio.LOW)
        elif a=='magenta':   # magenta
            gpio.digitalWrite(Pins['LedR'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedG'],gpio.LOW)
            gpio.digitalWrite(Pins['LedB'],gpio.HIGH)
        elif a=='cyan':      # cyan
            gpio.digitalWrite(Pins['LedR'],gpio.LOW)
            gpio.digitalWrite(Pins['LedG'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedB'],gpio.HIGH)
        else:                # yellow
            gpio.digitalWrite(Pins['LedR'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedG'],gpio.HIGH)
            gpio.digitalWrite(Pins['LedB'],gpio.LOW)

def signal_handler(signal, frame):
    print 'got SIGTERM'
    pygame.quit()
    sys.exit()

# Global stuff -------------------------------------------------------------

t = threading.Thread(target=timeLapse)
busy            = False
threadExited    = False

backlightState=1

screenMode      =  0      # Current screen mode; default = viewfinder
screenModePrior = -1      # Prior screen mode (for detecting changes)
returnScreen    = 0
iconPath        = 'icons' # Subdirectory containing UI bitmaps (PNG format)

lastRGB         = ""
doneNotify      = time.time()  # set done notify to now

blackfont = (0, 0, 0)
whitefont = (255, 255, 255)
fontcolour = whitefont
smallfont = 24
mediumfont = 30
largefont = 50

numeric         = 0       # number from numeric keypad
numberstring	= '0'

# GPIO Pin Assignment (piWiring - RPi B v2)
# Regular  02 03 04 07 08 09 10 11 14 15 17 18 22 23 24 25 27
# Extended 28 29 30 31
# screen pins               07 08 09 10 11 24 25
# used pins                 02 03 17 18 22 27
# used extended pins        28 29 30
# available pins            04(GPCLK0) 14(TXD) 15(RXD) 23
# available extended pins   31

Pins = {'Shutter': 02,
        'Focus': 03,
        'MotorA1': 17,
        'MotorA2': 18,
        'MotorB1': 22,
        'MotorB2': 27,
        'LedR': 28,                # waiting
        'LedG': 29,                # running
        'LedB': 30}                # done

backlightpin    = 252

#Stepper motor drive sequence
#Full step
forwardSeq = ['1000', '0001', '0100', '0010']
reverseSeq = ['0110', '0101', '1001', '1010']
stepFactor = 4
#Half step
#forwardSeq = ['1010', '1000', '1001', '0001', '0101', '0100', '0110', '0010']
#reverseSeq = ['0010', '0110', '0100', '0101', '0001', '1001', '1000', '1010']
#stepFactor = 8

consumedTime   = 0.0
startTime = 0.0

# fall back defaults - to be removed
settlingTime   = 0.2
shutterTime    = 2.0

# defined focus pause in milliseconds
focusPause     = 0.3

taskIndicator  = 'done'
lastTask       = taskIndicator

dictIdx	    = 'Shutter'

slideBasic = {'MaxLeft': 100,
              'MaxRight': 0,
              'MaxCCW': 100,
              'MaxCW': 0,
              'StepTime': 0.020,
              'MinDelay': 0.002,
              'Steps': 10}

# seconds      -> MinDelay, StepTime
# steps        -> MaxLeft, MaxRight, MaxCCW, MaxCW, Steps

# MaxLeft, MaxRight -> set on basic set up
# MinDelay -> minimum to run stepper motor (set here)
# Steps -> steps to take for basic setup (entered)

slideState= {'State': 0,
    'Shutter': 2.0,
    'Timespan': 60,
    'Images': 120,
    'Settle': 1,
    'Delay': 30,
    'ShootTime': 3600,
    'Left': 100,
    'Right': 0,
    'CCW': 100,
    'CW': 0,
    'PulseSteps': 1,
    'Position': 10,
    'Rotation': 0,
    'CurrentImage': 0,
    'Sliding': False,
    'DirectionLeft': True,
    'Calling': 'default'}
# state indicates whether times is complete (0) or in progress (1)

# minutes      -> Timespan
# seconds      -> Shutter, Settle, Shoottime
# milliseconds -> Delay (during motor step)
# count        -> Images, CurrentImage
# steps        -> Left, Right, PulseSteps, Position, CCW, CW, Rotation

# alternate icons to denote progress and toggled options
siStart = 0
siStop = 1
motorDirection = 1

aiKeypad = { 'Shutter': pygame.image.load(iconPath + '/shutter.png'),
       'Timespan': pygame.image.load(iconPath + '/timespan.png'),
       'Images': pygame.image.load(iconPath + '/images.png'),
       'Settle': pygame.image.load(iconPath + '/settle.png'),
       'Steps': pygame.image.load(iconPath + '/steps.png'),
       'Left': pygame.image.load(iconPath + '/leftpgm.png'),
       'Right': pygame.image.load(iconPath + '/rightpgm.png'),
       'MaxLeft': pygame.image.load(iconPath + '/leftrail.png'),
       'MaxRight': pygame.image.load(iconPath + '/rightrail.png')}

aiProgress = {'stopping': pygame.image.load(iconPath + '/stopping.png'),
      'settling': pygame.image.load(iconPath + '/settling.png'),
      'fire': pygame.image.load(iconPath + '/fire.png'),
      'travel': pygame.image.load(iconPath + '/travel.png'),
      'done': pygame.image.load(iconPath + '/blank60.png')}

aiSpin = {1: pygame.image.load(iconPath + '/spin/travel1.png'),
          2: pygame.image.load(iconPath + '/spin/travel2.png'),
          3: pygame.image.load(iconPath + '/spin/travel3.png'),
          4: pygame.image.load(iconPath + '/spin/travel4.png'),
          5: pygame.image.load(iconPath + '/spin/travel5.png'),
          6: pygame.image.load(iconPath + '/spin/travel6.png'),
          7: pygame.image.load(iconPath + '/spin/travel7.png'),
          8: pygame.image.load(iconPath + '/spin/travel8.png'),
          9: pygame.image.load(iconPath + '/spin/travel9.png'),
          10: pygame.image.load(iconPath + '/spin/travel10.png'),
          11: pygame.image.load(iconPath + '/spin/travel11.png'),
          12: pygame.image.load(iconPath + '/spin/travel12.png')}
spinIt = 1

aiRecover = {0: pygame.image.load(iconPath + '/start.png'),
      1:pygame.image.load(iconPath + '/stop.png'),
      2:pygame.image.load(iconPath + '/restart.png'),
      3:pygame.image.load(iconPath + '/reset.png')}

aiRail = {6: pygame.image.load(iconPath + '/camerarail.png'),
          4:pygame.image.load(iconPath + '/programrail.png')}

aiXposition = {0: 0,
               1: 290}
aiYposition = {0: 60,
               1: 60}

aiSmallDirection = {0: pygame.image.load(iconPath + '/leftlittle.png'),
                    1:pygame.image.load(iconPath + '/rightlittle.png')}
aiSDposition = {0: 5,
                1: 285}

icons = [] # This list gets populated at startup

# buttons[] is a list of lists; each top-level list element corresponds
# to one screen mode (e.g. viewfinder, image playback, storage settings),
# and each element within those lists corresponds to one UI button.
# There's a little bit of repetition (e.g. prev/next buttons are
# declared for each settings screen, rather than a single reusable
# set); trying to reuse those few elements just made for an ugly
# tangle of code elsewhere.

buttons = [

  # Screen mode 0 is main view screen of current status
  [Button((  0,  0, 60, 60), bg='leftPend',  cb=slideCallback, value=3),
   Button((260,  0, 60, 60), bg='rightPend', cb=slideCallback, value=4),
   Button((  0,180,120, 60), bg='starthold', cb=startCallback, value=1),
   Button((130,180, 60, 60), bg='gear',      cb=viewCallback, value=1),
   Button((200,180,120, 60), bg='stophold',  cb=startCallback, value=0),
   Button((  0, 60,320,120), bg='bigbutton', cb=backlightCallback, value=0)],

  # Screen 1 for changing values and setting motor direction
  [Button((0,    0, 60, 60), bg='shutter',      cb=valuesCallback, value=1),
   Button((0,   60, 60, 60), bg='timespan',     cb=valuesCallback, value=2),
   Button((0,  120, 60, 60), bg='images',       cb=valuesCallback, value=3),
   Button((0,  180, 60, 60), bg='settle',       cb=valuesCallback, value=5),
   Button((200,  0,120, 60), bg='shutdown',     cb=viewCallback, value=7),
   Button((200, 60,120, 60), bg='travelrail',   cb=viewCallback, value=4),
   Button((200,120,120, 60), bg='rotatecamera', cb=viewCallback, value=5),
   Button((200,180,120, 60), bg='done',         cb=valuesCallback, value=-1)],

  # Screen 2 for numeric input
  [Button((  0,  0,320, 60), bg='box'),
   Button((180,120, 60, 60), bg='0',         cb=numericCallback, value=0),
   Button((  0,180, 60, 60), bg='1',         cb=numericCallback, value=1),
   Button((120,180, 60, 60), bg='3',         cb=numericCallback, value=3),
   Button(( 60,180, 60, 60), bg='2',         cb=numericCallback, value=2),
   Button((  0,120, 60, 60), bg='4',         cb=numericCallback, value=4),
   Button(( 60,120, 60, 60), bg='5',         cb=numericCallback, value=5),
   Button((120,120, 60, 60), bg='6',         cb=numericCallback, value=6),
   Button((  0, 60, 60, 60), bg='7',         cb=numericCallback, value=7),
   Button(( 60, 60, 60, 60), bg='8',         cb=numericCallback, value=8),
   Button((120, 60, 60, 60), bg='9',         cb=numericCallback, value=9),
   Button((240,120, 80, 60), bg='clear',     cb=numericCallback, value=10),
   Button((180,180,140, 60), bg='update',    cb=numericCallback, value=12),
   Button((180, 60,140, 60), bg='bigcancel', cb=numericCallback, value=11)],

  # Screen 3 for numeric input
  [Button((  0,  0,320, 60), bg='box'),
   Button((180,120, 60, 60), bg='0',         cb=numericCallback, value=0),
   Button((  0,180, 60, 60), bg='1',         cb=numericCallback, value=1),
   Button((120,180, 60, 60), bg='3',         cb=numericCallback, value=3),
   Button(( 60,180, 60, 60), bg='2',         cb=numericCallback, value=2),
   Button((  0,120, 60, 60), bg='4',         cb=numericCallback, value=4),
   Button(( 60,120, 60, 60), bg='5',         cb=numericCallback, value=5),
   Button((120,120, 60, 60), bg='6',         cb=numericCallback, value=6),
   Button((  0, 60, 60, 60), bg='7',         cb=numericCallback, value=7),
   Button(( 60, 60, 60, 60), bg='8',         cb=numericCallback, value=8),
   Button((120, 60, 60, 60), bg='9',         cb=numericCallback, value=9),
   Button((240,120, 80, 60), bg='clear',     cb=numericCallback, value=10),
   Button((180,180, 60, 60), bg='second',    cb=numericCallback, value=13),
   Button((240,180, 80, 60), bg='fraction',  cb=numericCallback, value=14),
   Button((180, 60,140, 60), bg='bigcancel', cb=numericCallback, value=11)],

  # Screen 4 set end points
  [Button((  0,  0, 60, 60), bg='',           cb=positionCallback, value=1),
   Button(( 60,  0, 60, 60), bg='',           cb=positionCallback, value=3),
   Button((200,  0, 60, 60), bg='',           cb=positionCallback, value=4),
   Button((260,  0, 60, 60), bg='',           cb=positionCallback, value=2),
   Button((  0,120, 60, 60), bg='left',       cb=slideCallback, value=1),
   Button(( 60,120, 60, 60), bg='leftend',    cb=slideCallback, value=5),
   Button((120,120, 80, 60), bg='direction',  cb=slideCallback, value=0),
   Button((200,120, 60, 60), bg='rightend',   cb=slideCallback, value=6),
   Button((260,120, 60, 60), bg='right',      cb=slideCallback, value=2),
   Button((  0,180,120, 60), bg='cancel',     cb=programCallback, value=0),
   Button((130,180, 60, 60), bg='gear',       cb=viewCallback, value=6),
   Button((200,180,120, 60), bg='done',       cb=programCallback, value=-1)],

  # Screen 5 set rotate - holding menu for future use
  [
   Button((  0,180,120, 60), bg='cancel',     cb=setspeedCallback, value=0),
   Button((200,180,120, 60), bg='done',       cb=setspeedCallback, value=-1)],

  # Screen 6 set end of travel
  [
   Button((  0,  0, 60, 60), bg='',           cb=positionCallback, value=5),
   Button(( 60,  0, 60, 60), bg='',           cb=positionCallback, value=7),
   Button((200,  0, 60, 60), bg='',           cb=positionCallback, value=8),
   Button((260,  0, 60, 60), bg='',           cb=positionCallback, value=6),
   Button((  0, 60, 60, 60), bg='setspeed',   cb=setspeedCallback, value=1),
   Button((260, 60, 60, 60), bg='steps',      cb=setspeedCallback, value=3),
   Button((  0,120, 60, 60), bg='left',       cb=slideCallback, value=1),
   Button((260,120, 60, 60), bg='right',      cb=slideCallback, value=2),
   Button((  0,180,120, 60), bg='cancel',     cb=setspeedCallback, value=0),
   Button((200,180,120, 60), bg='done',       cb=setspeedCallback, value=-1)],

  # Screen 7 set, clear and save programs
  [
   Button((  0,  0, 64, 64), bg='P1',          cb=programCallback, value=1),
   Button(( 64,  0, 64, 64), bg='P2',          cb=programCallback, value=2),
   Button((128,  0, 64, 64), bg='P3',          cb=programCallback, value=3),
   Button((192,  0, 64, 64), bg='P4',          cb=programCallback, value=4),
   Button((256,  0, 64, 64), bg='Current',     cb=programCallback, value=5),
   Button((  0,180,100, 60), bg='smallCancel', cb=programCallback, value=0),
   Button((100,180, 60, 60), bg='clr',         cb=programCallback, value=6),
   Button((160,180, 60, 60), bg='save',        cb=programCallback, value=7),
   Button((220,180,100, 60), bg='use',         cb=programCallback, value=-1)],

  # Screen 7 shutdown
  [Button((  0,  0,320, 80), bg='return',     cb=shutdownPi, value=-1),
   Button((  0,160,320, 80), bg='shutdown',   cb=shutdownPi, value=1)]
]


# Assorted utility functions -----------------------------------------------
def debugState(s):
    print "slideState from.............." + s
    print "slideState['State'].........." + str(slideState['State'])
    print "slideState['Shutter']........" + str(slideState['Shutter'])
    print "slideState['Timespan']......." + str(slideState['Timespan'])
    print "slideState['Images']........." + str(slideState['Images'])
    print "slideState['Settle']........." + str(slideState['Settle'])
    print "slideState['Delay'].........." + str(slideState['Delay'])
    print "slideState['ShootTime']......" + str(slideState['ShootTime'])
    print "slideState['Left']..........." + str(slideState['Left'])
    print "slideState['Right'].........." + str(slideState['Right'])
    print "slideState['PulseSteps']....." + str(slideState['PulseSteps'])
    print "slideState['Position']......." + str(slideState['Position'])
    print "slideState['CurrentImage']..." + str(slideState['CurrentImage'])
    if slideState['Sliding']:
        print "slideState['Sliding']........True"
    else:
        print "slideState['Sliding']........False"
    if slideState['DirectionLeft']:
        print "slideState['DirectionLeft']..True"
    else:
        print "slideState['DirectionLeft']..False"
    
def debugBasic(s):
    print "slideBasic from............" + s
    print "slideBasic['MaxLeft']......" + str(slideBasic['MaxLeft'])
    print "slideBasic['MaxRight']....." + str(slideBasic['MaxRight'])
    print "slideBasic['StepTime']....." + str(slideBasic['StepTime'])
    print "slideBasic['MinDelay']....." + str(slideBasic['MinDelay'])
    print "slideBasic['Steps']........" + str(slideBasic['Steps'])

def saveBasic():
    global slideBasic
    #debug
    #debugBasic('saveBasic')
    #debug
    try:
      outfile = open('slideRBasic.pkl', 'wb')
      # Use a dictionary (rather than pickling 'raw' values) so
      # the number & order of things can change without breaking.
      pickle.dump(slideBasic, outfile)
      outfile.close()
    except:
      pass
    
def loadBasic():
    global slideBasic
    #debugBasic('loadBasic')
    try:
      infile = open('slideRBasic.pkl', 'rb')
      slideBasic = pickle.load(infile)
      infile.close()
    except:
      pass
    
def saveState(s):
    global slideState
    slideState['Calling'] = s
    #debug
    #debugState('saveState ' + s)
    #debug
    try:
      outfile = open('slideRState.pkl', 'wb')
      # Use a dictionary (rather than pickling 'raw' values) so
      # the number & order of things can change without breaking.
      pickle.dump(slideState, outfile)
      outfile.close()
    except:
      pass

def loadState():
    global slideState
    #debug
    #debugState('loadState')
    #debug
    try:
      infile = open('slideRState.pkl', 'rb')
      slideState= pickle.load(infile)
      infile.close()
    except:
      pass

# Initialization -----------------------------------------------------------

# Init framebuffer/touchscreen environment variables
os.putenv('SDL_VIDEODRIVER', 'fbcon')
os.putenv('SDL_FBDEV'      , '/dev/fb1')
os.putenv('SDL_MOUSEDRV'   , 'TSLIB')
os.putenv('SDL_MOUSEDEV'   , '/dev/input/touchscreen')

# --------
try:  # capture exceptions

    # Set up GPIO pins
    print "Init GPIO pins..."
    gpio = wiringpi2.GPIO(wiringpi2.GPIO.WPI_MODE_GPIO)
    gpio.pinMode(Pins['Shutter'],gpio.OUTPUT)
    gpio.pinMode(Pins['Focus'],gpio.OUTPUT)
    gpio.pinMode(Pins['LedR'],gpio.OUTPUT)
    gpio.pinMode(Pins['LedB'],gpio.OUTPUT)
    gpio.pinMode(Pins['LedG'],gpio.OUTPUT)
    gpio.pinMode(Pins['MotorA1'],gpio.OUTPUT)
    gpio.pinMode(Pins['MotorA2'],gpio.OUTPUT)
    gpio.pinMode(Pins['MotorB1'],gpio.OUTPUT)
    gpio.pinMode(Pins['MotorB2'],gpio.OUTPUT)

    # set external LED to start value
    #print 'setLED 1'
    setLED('start')
    
    # I couldnt seem to get at pin 252 for the backlight using the usual method above,
    # but this seems to work
    os.system("echo 252 > /sys/class/gpio/export")
    os.system("echo 'out' > /sys/class/gpio/gpio252/direction")
    os.system("echo '1' > /sys/class/gpio/gpio252/value")

    # Init pygame and screen
    print "Initting..."
    pygame.init()
    print "Setting Mouse invisible..."
    pygame.mouse.set_visible(False)
    print "Setting fullscreen..."
    modes = pygame.display.list_modes(16)
    screen = pygame.display.set_mode(modes[0], FULLSCREEN, 16)

    print "Loading Icons..."
    # Load all icons at startup.
    for file in os.listdir(iconPath):
      if fnmatch.fnmatch(file, '*.png'):
        icons.append(Icon(file.split('.')[0]))
        
    # Assign Icons to Buttons, now that they're loaded
    print"Assigning Buttons"
    for s in buttons:        # For each screenful of buttons...
      for b in s:            #  For each button on screen...
        for i in icons:      #   For each icon...
          if b.bg == i.name: #    Compare names; match?
            b.iconBg = i     #     Assign Icon to Button
            b.bg     = None  #     Name no longer used; allow garbage collection
          if b.fg == i.name:
            b.iconFg = i
            b.fg     = None

    print"Load Settings"
    # Get settings from pickle, validate them and set timelapse execution
    loadState() # Must come last; fiddles with Button/Icon states
    reasonableValues() # Validate that the execution parms make sense
    if timelapseSettings():         # Calculate timelapse execution values
        taskIndicator = 'stopping'

    print "loading background.."
    img    = pygame.image.load('icons/slideR.png')

    # define the screen background from the image
    if img is None or img.get_height() < 240: # Letterbox, clear background
      screen.fill(0)
    if img:
      screen.blit(img,
        ((320 - img.get_width() ) / 2,
         (240 - img.get_height()) / 2))
    # show the screen
    pygame.display.update()
    time.sleep(1)

    # Main loop ----------------------------------------------------------------

    signal.signal(signal.SIGTERM, signal_handler)

    # recover from interupt (unplanned power outage)
    if slideState['State'] > 0:
        siStart = 2
        siStop = 3
        
    print "mainloop.."
    while True:
    # loop until ^C
    #
    # 1. capture event loop until button pushed, not on screen 0 or screen change
    # 2. blit background
    # 3. draw icons and buttons based on current screen
    # 4. blit screen specific output based on current screen - screenMode
    # 5. screen update
    # 6. set screenModePrior to current screenMode

        # on completion of timelapse set 'ready' LED on after 'done' LED
        if busy == False:
            if time.time() > doneNotify:
                #print 'setLED 2'
                setLED('ready')
            else:
                #print 'setLED 3'
                setLED('done')
            
        # Process touchscreen input
        while True:

            for event in pygame.event.get():
              if(event.type is MOUSEBUTTONDOWN):
                pos = pygame.mouse.get_pos()
                for b in buttons[screenMode]:
                  if b.selected(pos): break
              # why shut off the motor on mouse up ??????????
#              elif(event.type is MOUSEBUTTONUP):
#                slideState['Sliding'] = 0
#                travelRail(slideBasic['MinDelay'],0)
#                saveState()

            # if not on screen 0 or changing screens then leave event loop
            if screenMode >= 0 or screenMode != screenModePrior: break

            # first time through break out due to screen mode !=
            # break out when not on screen 0
            # break out when changing screens (effect only affects screen 1->0 flow)

        if img is None or img.get_height() < 240: # Letterbox, clear background
            screen.fill(0)
        if img:
            screen.blit(img,
              ((320 - img.get_width() ) / 2,
              (240 - img.get_height()) / 2))

        # Overlay buttons on display and update
        for i,b in enumerate(buttons[screenMode]):
            b.draw(screen)

        if slideState['DirectionLeft']:
            motorDirection = 0
        else:
            motorDirection = 1

    # basic slide setup
        if screenMode == 6:
            myfont = pygame.font.SysFont('Arial', smallfont)
            myfont.set_bold(True)
            # left end point
            labeltext = str(slideBasic['MaxLeft'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 10))
            # current position
            labeltext = str(slideState['Position'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,0,screenMode,myfont), 10))
            # right end point
            labeltext = str(slideBasic['MaxRight'])
            label = myfont.render(labeltext , 0, (fontcolour))
            screen.blit(label, (xPos(labeltext,1,screenMode,myfont), 10))
            # total steps, step speed, travel per step
            labeltext = str(round(slideBasic['StepTime'] * 1000,1)) + 'ms'
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 75))
            # steps to take
            labeltext = str(slideBasic['Steps'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,1,screenMode,myfont), 75))

        #   show the rail
            screen.blit(aiRail[screenMode], (0,0))

    # slide set up screen
        if screenMode == 4:
            myfont = pygame.font.SysFont('Arial', smallfont)
            myfont.set_bold(True)
            labeltext = str(slideState['Left'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 10))
            
            labeltext = str(slideState['Position'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,0,screenMode,myfont), 10))

            labeltext = str(slideState['Right'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,1,screenMode,myfont), 10))
            
            timeOnSlide = ((((slideBasic['StepTime'] + slideBasic['MinDelay']) *
                          (slideState['Left'] - slideState['Right'])) / 1000) +
                          slideState['ShootTime'])
            sec = timedelta(seconds=int(timeOnSlide))
            d = datetime(1,1,1) + sec
            if d.hour > 0:
                labeltext = '%dh%dm%ds' % (d.hour, d.minute, d.second)
            else:
                labeltext = '%dm%ds' % (d.minute, d.second)
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,0,screenMode,myfont), 70))
        #   show the rail
            screen.blit(aiRail[screenMode], (0,0))
        #   show the motor direction
            screen.blit(aiSmallDirection[motorDirection], (aiXposition[motorDirection],aiYposition[motorDirection]))
            
    # keypad screens
        if screenMode == 3 or screenMode == 2:
            myfont = pygame.font.SysFont('Arial', largefont)
            myfont.set_bold(False)
            label = myfont.render(numberstring , 1, (fontcolour))
            screen.blit(label, (xPos(numberstring,-1,screenMode,myfont), 2))
            # blit the icon of the button pushed to get here
            screen.blit(aiKeypad[dictIdx], (260, 0))

        # parameter screen
        if screenMode == 1:
            myfont = pygame.font.SysFont('Arial', smallfont)
            myfont.set_bold(True)

            sValue = float(slideState['Shutter'])
            if (sValue < 1):
                numeric = int(1 / sValue)
                labeltext = '1/' + str(numeric) + 's'
            else:
                numeric = int(sValue)
                labeltext = str(numeric) + 's'
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 10))

            labeltext = str(slideState['Timespan']) + 'min'
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 70))

            labeltext = str(slideState['Images'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 130))

            sValue = float(slideState['Settle'])
            if (sValue == 0):
                numeric = int(sValue)
                label = myfont.render(str(numeric) + 's' , 1, (fontcolour))
            elif (sValue < 1):
                numeric = int(1 / sValue)
                labeltext = '1/' + str(numeric) + 's'
            else:
                numeric = int(sValue)
                labeltext = str(numeric) + 's'
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 190))

        # initial (home) screen
        if screenMode == 0:
            # blit start / stop or restart / reset buttons
            screen.blit(aiRecover[siStart], (  0,180))
            screen.blit(aiRecover[siStop], (200,180))
            myfont = pygame.font.SysFont('Arial', mediumfont)
            myfont.set_bold(False)
            # blit status icon
            if taskIndicator == 'travel':
                screen.blit(aiSpin[spinIt], (130, 2))
                spinIt = spinIt + 1
                if spinIt > 12: spinIt = 1
            elif taskIndicator != lastTask:
                spinIt = 1
                screen.blit(aiProgress[taskIndicator], (130, 2))

            sValue = float(slideState['Shutter'])
            if (sValue < 1):
                numeric = int(1 / sValue)
                labeltext = '1/' + str(numeric) + 's'
            else:
                numeric = int(sValue)
                labeltext = str(numeric) + 's'
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 70))

            sValue = float(slideState['Settle'])
            if (sValue < 1):
                numeric = int(1 / sValue)
                labeltext = '1/' + str(numeric) + 's'
            else:
                numeric = int(sValue)
                labeltext = str(numeric) + 's'
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,1,screenMode,myfont), 70))
            
        #   images remaining
            labeltext = str(slideState['CurrentImage']) + ' of ' + str(slideState['Images'])
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,-1,screenMode,myfont), 110))
            
        #   time remaining
            consumedTime = 0.0
            if busy:
                consumedTime = time.time() - startTime
            remaining = round((float(slideState['Timespan']) * 60) - consumedTime,1)
            if remaining > 0:
                sec = timedelta(seconds=int(remaining))
            else:
                sec = timedelta(seconds=int(slideState['Timespan'] * 60))
            d = datetime(1,1,1) + sec
            if d.hour > 0:
                labeltext = '%dh%dm%ds' % (d.hour, d.minute, d.second)
            else:
                labeltext = '%dm%ds' % (d.minute, d.second)
            label = myfont.render(labeltext , 1, (fontcolour))
            screen.blit(label, (xPos(labeltext,1,screenMode,myfont), 110))
        #   show the motor direction
            screen.blit(aiSmallDirection[motorDirection], (aiSDposition[motorDirection],150))

        pygame.display.update()

        screenModePrior = screenMode

except KeyboardInterrupt:
    # here you put any code you want to run before the program
    # exits when you press CTRL+C
    print "."
    print "CTRL+C Out!"
      
#except:
    # this catches ALL other exceptions including errors.
    # You won't get any error messages for debugging
    # so only use it once your code is working
    #    print "Other error or exception occurred!"
      
finally:
#   GPIO.cleanup() # this ensures a clean exit
    gpioCleanup
    print "Done"
