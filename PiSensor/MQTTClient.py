
import time, threading, traceback
# MQTT Library, speficially client
import paho.mqtt.client as mqtt
# Pi GPIO Control
import RPi.GPIO as GPIO

# Equiv of constants
# MQTT Settings
__SERVER__ = "iot.eclipse.org"
__PORT__ = 1883
__MQTT_TOPIC_CONTROL__ = "/testing/dja33/public/control"
__MQTT_TOPIC_MESSAGE__ = "/testing/dja33/public/message"
# MQTT Control messages
__CONTROL_START_MSG__ = "pi:u:s"
__CONTROL_END_MSG__   = "pi:u:e"
__CONTROL_ALARM_MSG__ = "pi:u:a"

# GPIO settings
__GPIO_PIN__ = 16

# Seconds to wait between sending heartbeats
__HEARTBEAT_SLEEP_SECONDS__ = 15

# Global vars
client = mqtt.Client()
connected = 0 # Connection state, 0 = nothing, 1 = connected

# GPIO (General purpose input/output) settings
GPIOenabled = True # GPIO interrupt enabled, yes or no
GPIObouncetime = 300 # How long to wait before accepting new interrupts (Milliseconds)
GPIOthreshold = 3 # How many consecutive signals we must receive to trigger our state
GPIOdelaycounter = 0.5 # Seconds between each decrement of a 'count'
GPIOtimer = None # Object wrapper for Thread manipulation of counting

hbThread = None # Thread used for sending heartbeats periodically 
alive = True # Whether the heartbeat thread is alive

class GPIOTimer:

	# defines that these are the ONLY self referencing fields
	# for this class/object
	__slots__ = ("_stage", "_delay", "_thread",
			 "_count", "_maxcount", "_stop")

	def __init__(self):
		self._delay = GPIOdelaycounter
		self._stage = 0
		self._thread = None
		self._count = 5	
		self._maxcount = 5
		self._stop = False

	# Checking function to be used as a means of measuring the
	# validity of the GPIO input
	def _validate(self):
		while self._stop == False:
			#print( " Sleep: {} | C: {} ".format(((GPIObouncetime / 1000) + self._delay), self._count))
			time.sleep((GPIObouncetime / 1000) + self._delay)
			if self._count > 0:
				self._count = self._count - 1
			if self._count == 0:
				self._count = self._maxcount
				self._stage = 0

	# Start the thread, which in turns utilises the _validate function
	def startTimer(self):
		self.stopTimer()
		self._stop = False
		self._thread = threading.Thread(target=self._validate, args=())
		self._thread.daemon = True 
		self._thread.start()

	# Stop the thread gracefully, utilises a condition rather than 
	# forcefully stopping the thread
	def stopTimer(self):
		# If the thread actually is alive
		if not self._thread == None and self._thread.is_alive():
			self._stop = True
			# Sleeping the same amount of time expected 
			time.sleep((GPIObouncetime / 1000) + self._delay)
		self._count = self._maxcount
		self._stage = 0

	def get_frequency(self):
		return GPIOthreshold

	# Get the current sensitivity of the GPIO reporter
	def get_sensitivity(self):
		return (GPIObouncetime / 1000) + self._delay	

	def _increase_sensitivity(self):
		self._delay = self._delay + 0.1
		self._maxcount = self._maxcount + 1

	# Decrease the sensitiivty, lowers values of delay between check 
	# in validation as well as the maximum amount of counts expected
	# Cannot fall below 0.15 or 1
	def _decrease_sensitivity(self):
		if self._delay > 0.15:
			self._delay = self._delay - 0.1
		if self._maxcount > 1:
			self._maxcount = self._maxcount - 1

	def change_sensitivity(self, val):
		if val > 0:
			self._increase_sensitivity()
		else:
			self._decrease_sensitivity()

	# Called when the sensor picks up noise and wants to validate it
	def increment(self):
		print ("Increment Received: {}".format(self._stage))
		self._stage = self._stage + 1
		# If the noise received extends beyond the threshold
		if self._stage > GPIOthreshold:
			publish_message(__CONTROL_ALARM_MSG__)
			self._stage = 0
		self._count = self._maxcount

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
	print("Connected with result code: {}".format(str(rc)))
    	# Subscribing in on_connect() means that if we lose the connection and
    	# reconnect then subscriptions will be renewed.
	client.subscribe(__MQTT_TOPIC_CONTROL__)
	# Tell control server we're online
	publish_message(__CONTROL_START_MSG__)

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
	print ("{} -> {}".format(msg.topic, msg.payload))
	# Acknowledge request
	# Mid = message ID
	# Result = Successful or not
	#(result, mid) = client.publish(__MQTT_TOPIC_MESSAGE__, "Ackn", 1, True)
	
	# Split the incoming packet
	split = msg.payload.split(":")
	
	# invalid payload
	if len(split) <= 1:
		return
	# not our message
	if not split[0] == "pi":
		return
	
	# Reference our global variables
	global GPIOthreshold
	global GPIObouncetime

	# inspecting contents of message
	cmd = split[1]

	# Disable interrupt, timer etc
	if cmd == "d":
		if GPIOenabled == False: 
			print ("Sensor already disabled.") 
		else:
			disable_interrupts()
		return
	# Enable interrupt, timer etc
	elif cmd == "e":
		if GPIOenabled == True:
			print ("Sensor already enabled.")
		else:
			enable_interrupts()
		return

	# Try to parse the attribute value as an integer
	attribute = 0
	try:
		attribute = int(split[2]) 
	except:
		print ("Unexpected format for attribute, expecting int received: {}".format(split[2]))
		return	

	if cmd == "s": # Sensitivity modifier
		GPIOtimer.change_sensitivity(attribute)
		print ("Sensitivity change: {} | {}".format(attribute, GPIOtimer.get_sensitivity()))
		publish_message("pi:s:{}".format(GPIOtimer.get_sensitivity()))
	elif cmd == "f": # Frequency modifier
		if attribute > 0 and GPIOthreshold < 10:
			GPIOthreshold = GPIOthreshold + 1
		elif GPIOthreshold > 1:
			GPIOthreshold = GPIOthreshold - 1
		print ("Frequency change: {} | {}".format(attribute, GPIOthreshold))
		publish_message("pi:t:{}".format(GPIOthreshold))
	elif cmd == "b": # Bounce time modifier
		if GPIObouncetime <= 100 and attribute <= 0:
			return # No point updating the interrupt, will be at 100
		if attribute == 0:
			attribute = -1
		GPIObouncetime = GPIObouncetime + (attribute * 10)
		if GPIObouncetime < 100:
			GPIObouncetime = 100
		update_interrupt_settings()		
	else:
		print ("Unknown cmd '{}'.".format(cmd))

# Callback function for GPIO interrupt
def on_noise_break(channel):  
	GPIOtimer.increment()

# Shorthand function for publishing messages
def publish_message(msg):
	(result, mid) = client.publish(__MQTT_TOPIC_MESSAGE__, msg, 1, True)
	print ("Mid: {} | Result: {} | Contents: {}".format(mid, result, msg))

# Disable GPIO interrupts, stops GPIOTimer thread
def disable_interrupts():
	global GPIOenabled
	print ("Disabling interrupts.")
	GPIO.remove_event_detect(__GPIO_PIN__) 
	GPIOtimer.stopTimer()
	GPIOenabled = False
	# ...

# Attempt to reestablish interrupts, will try 5 times
# before giving up and raise an exception
# If successful then starts the GPIOtimer thread
def enable_interrupts():
	global GPIOenabled
	print ("Enabling interrupts.")
	retry = 5
	while retry >= 0:
		try:
			GPIO.setmode(GPIO.BOARD)
			GPIO.setup(__GPIO_PIN__, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
			GPIO.add_event_detect(__GPIO_PIN__, GPIO.FALLING, callback=on_noise_break, bouncetime=GPIObouncetime)
			break
		except:
			if retry == 0:
				print ("Giving up... :[ ")
				raise
			print ("Failed to attach event to GPIO, trying again... {}".format(retry))
			time.sleep(1)
			retry = retry - 1
	GPIOtimer.startTimer()
	GPIOenabled = True
	# ...

# Disable and re-enable interrupts with new parameters
def update_interrupt_settings():
	disable_interrupts()
	# Let GPIO catch up
	# time.sleep(3)
	GPIO.cleanup()
	enable_interrupts()
	print ("Updated interrupt settings, bouncetime: {}".format(GPIObouncetime))
	publish_message("pi:b:{}".format(GPIObouncetime))

def heartbeat():
	while alive:
		time.sleep(__HEARTBEAT_SLEEP_SECONDS__) # Sleep for 15 seconds
		publish_message("pi:*")

# Start 
try:

	print ("Started Program...")
	print ("ControlTopic: {}".format(__MQTT_TOPIC_CONTROL__))
	print ("MessageTopic: {}".format(__MQTT_TOPIC_MESSAGE__))
	client = mqtt.Client()
	print ("Setting callbacks for receiving and connection...")
	client.on_connect = on_connect
	client.on_message = on_message
	print ("Connecting to '{}:{}' ...".format(__SERVER__, __PORT__))
	client.connect(__SERVER__, __PORT__, 60)
	print (" % ...")
	print ("Setting up GPIO pins...")
	GPIOtimer = GPIOTimer()
	enable_interrupts()		
	
	# Heartbeat start
	hbThread = threading.Thread(target=heartbeat)
	hbThread.start()

	print ("Ready to receive/transmit. CRTL+C to terminate.")

	# Blocking call that processes network traffic, dispatches callbacks and
	# handles reconnecting.
	client.loop_forever()

# CRTL + C
except KeyboardInterrupt:
	print(" Keyboard interrupt, exiting...") 
# Something else went wrong
except BaseException as e:
	print(" An error occurred. Exiting...") 
	print (e)
	traceback.print_exc()

# Program closed
alive = False
publish_message(__CONTROL_END_MSG__) # Tell the control unit we're finished
disable_interrupts() # Close up any interrupts left open
GPIO.cleanup()       # clean up GPIO on CTRL+C exit
quit()
