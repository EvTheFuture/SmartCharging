"""
    Smart Charging of EVs based on hourly price.
    Copyright (C) 2020    Magnus Sandin

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

Arguments in config file:

debug:                      O: yes | no (activate debug logging)
finish_at_latest_by:        M: Time String or entity id
price_data:                 M: Where to fetch the price info
    - entity:               M: entity_id[,attribute] to fetch price from
      required:             O: Is this required to start charging Yes | No
charger_switch:             M: Switch to start and stop EV charger
charging_state:             M: Sensor to read charging state from
charging_state_stopped:     O: String that indicate not charging
charging_state_charging:    O: String that indicate charging
charging_state_complete:    O: String that indicate charging complete
device_tracker:             O: Sensor indicating if EV is home
time_left:                  M: Time left in hours (float) entity_id[,attribute]


EXAMPLE:
charge_ev_when_cheepest:
    module: ev_charge_control
    class: SmartCharging
    finish_at_latest_by: "07:30"
    price_data:
        - entity: sensor.nordpool_kwh_se3_sek_3_1000_025,raw_today
          required: Yes
        - entity: sensor.nordpool_kwh_se3_sek_3_1000_025,raw_tomorrow
          required: Yes
    charger_switch: switch.model_3_charger_switch
    charging_state: binary_sensor.model_3_charger_sensor,charging_state
    charging_state_stopped: Stopped
    charging_state_charging: Charging
    charging_state_complete: Complete
    device_tracker: device_tracker.model_3_location_tracker
    time_left: sensor.model_3_charging_rate_sensor,time_left
    debug: yes
"""

VERSION = 0.12

# Store all attributes every day to disk
STORE_TO_FILE_EVERY = 60 * 60 * 24

DELAY_AFTER_STATE_CHANGE = 2.0
MAX_TIME_FOR_WORKER_TO_SLEEP = 3600  # 1 hour
ENTITIES = {
    "~_active": {
        "type": "switch",
        "state": "on",
        "attributes": {"friendly_name": "Smart Charging Active",},
    },
    "~_status": {
        "type": "sensor",
        "state": "unknown",
        "attributes": {
            "friendly_name": "Smart Charging Status",
            "charge_time_left": "unknown",
            "next_start": "unknown",
            "next_stop": "unknown",
            "slots": [],
            "reason": "",
        },
    },
}
import appdaemon.plugins.hass.hassapi as hass
import copy
import json
import queue
import threading

from datetime import datetime
from datetime import time
from dateutil import parser
from dateutil import tz


class SmartCharging(hass.Hass):

    data = {}

    def initialize(self):
        if "debug" in self.args and self.args["debug"]:
            self.set_log_level("DEBUG")

        self.log("Starting....")

        self.status_state = "uknown"
        self.status_attributes = copy.deepcopy(
            ENTITIES["~_status"]["attributes"]
        )

        # This is the file where we store current states
        # between restarts for this app
        self.persistance_file = (
            f"{self.config['config_dir']}/apps/{self.args['module']}/"
            + f"{self.name}.json"
        )

        self.load_persistance_file()

        self.initialize_entities()

        self.run_calculations_handle = None
        self.charge_time_needed = None

        self.status_complete = self.get_config_value(
            "charging_state_complete", "complete"
        ).lower()
        self.status_charging = self.get_config_value(
            "charging_state_charging", "charging"
        ).lower()
        self.status_stopped = self.get_config_value(
            "charging_state_stopped", "stopped"
        ).lower()

        self.abort = False
        self.worker_thread_event = threading.Event()

        # This thread handle recalculations when new price period starts and
        # when an entity attribute we listen for change. set self.abort to True
        # to stop execution of this thread
        self.worker_thread = threading.Thread(
            target=self.worker_thread, name="Worker Thread"
        )
        self.worker_thread.daemon = True
        self.worker_thread.start()

        self.setup_listener("switch." + self.name + "_active")
        self.setup_listener(self.args["charger_switch"])
        self.setup_listener(self.args["charging_state"])
        self.setup_listener(self.args["device_tracker"])
        self.setup_listener(self.args["time_left"])

        for pd in self.args["price_data"]:
            self.setup_listener(pd["entity"])

        # Save the current state of all covers every STORE_COVER_STATE_EVERY
        # seconds
        self.run_every(
            callback=self.save_persistance_file,
            start=f"now+{STORE_TO_FILE_EVERY}",
            interval=STORE_TO_FILE_EVERY,
        )

    def terminate(self):
        self.save_persistance_file()

        self.abort = True

        # Inform the worker thread to wake up
        self.worker_thread_event.set()

        # Wait for it to finish before we do
        self.worker_thread.join()
        self.debug("Finished clean up process, bye bye...")

    def load_persistance_file(self):
        """Load persistance data from file when app starts
        and initialize mandatory data if it doenst exist."""
        try:
            with open(self.persistance_file, "r") as f:
                self.data = json.load(f)

        except IOError as e:
            self.log(f"Persistance file {self.persistance_file} not found...")
            self.data = {}

        except Exception as e:
            self.error(f"Exception when loading persistance file: {e}")
            self.data = {}

        for k, v in ENTITIES.items():
            if k not in self.data:
                self.data[k] = v["state"]

    def save_persistance_file(self, kwargs=None):
        """Save persistance data to file"""
        try:
            with open(self.persistance_file, "w") as f:
                f.write(json.dumps(self.data, indent=4))

            self.log(f"Persistance entries written to {self.persistance_file}")

        except Exception as e:
            self.error(f"Exception when storing persistance file: {e}")
            return False

    def debug(self, text):
        self.get_main_log().debug(text)

    def initialize_entities(self):
        self.debug("Setting up entities")

        for k, v in ENTITIES.items():
            entity_id = v["type"] + "." + k.replace("~", self.name)
            self.debug(f"Will setup entity: {entity_id} -> {v}")

            self.set_state(
                entity_id, state=self.data[k], attributes=v["attributes"],
            )
            self.debug("Created entity")

    def get_entity_value(self, entity):
        e, a = self.get_entity_and_attribute(entity)

        return self.get_state(entity_id=e, attribute=a)

    def setup_listener(self, entity):
        e, a = self.get_entity_and_attribute(entity)

        self.listen_state(callback=self.new_state, entity=e, attribute=a)

    def new_state(self, entity, attribute, old, new, kwargs):
        self.debug(f"NEW STATE!! {entity}.{attribute} = {new} ({old})")

        if entity.endswith(self.name + "_active"):
            self.data["~_active"] = new

        # Delay trigger to avoid multiple calculations when multiple entities
        # change at the same time
        if self.run_calculations_handle is not None:
            self.cancel_timer(self.run_calculations_handle)

        self.debug("Scheduling Calculations...")
        self.run_calculations_handle = self.run_in(
            self.trigger_calculation, DELAY_AFTER_STATE_CHANGE
        )

    def trigger_calculation(self, kwargs=None):
        self.worker_thread_event.set()

    def worker_thread(self):
        while not self.abort:
            try:
                self.calculate()
            except Exception as e:
                self.get_main_log().exception("Unexpected exception...")

            # Just to make sure we cant get a negative number
            # we store the time before creating the price list
            now = self.get_now()

            price = self.get_price()
            if price is not None and len(price):
                sleep_time = (price[0]["end"] - now).total_seconds()
                if sleep_time > MAX_TIME_FOR_WORKER_TO_SLEEP:
                    sleep_time = MAX_TIME_FOR_WORKER_TO_SLEEP
            else:
                sleep_time = MAX_TIME_FOR_WORKER_TO_SLEEP

            time_when_going_to_sleep = self.get_now()
            self.debug(f"Will try to sleep for {sleep_time} seconds")
            self.worker_thread_event.wait(sleep_time)

            self.worker_thread_event.clear()

            time_slept = (
                self.get_now() - time_when_going_to_sleep
            ).total_seconds()

            self.debug(
                f"Woke up inside worker thread after {time_slept} seconds"
            )

    def calculate(self):
        self.status_attributes["charge_time_left"] = "unknown"
        self.status_attributes["next_start"] = ""
        self.status_attributes["next_stop"] = ""
        self.status_attributes["slots"] = []

        if self.data["~_active"] == "off":
            self.debug("Module is inactivated by user...")
            self.status_state = "inactive"
            self.status_attributes["reason"] = "Inactivated by user"
            self.update_status_entity()
            return

        if self.get_entity_value(self.args["device_tracker"]) != "home":
            self.debug("EV is not home, aborting calculation...")
            self.status_state = "disabled"
            self.status_attributes["reason"] = "EV is not home"
            self.update_status_entity()
            return

        cs = self.get_entity_value(self.args["charging_state"]).lower()
        tl = self.get_entity_value(self.args["time_left"])

        self.debug(f"Current state is: {cs}")
        if cs == self.status_charging:
            if tl > 0:
                self.charge_time_needed = int(tl * 3600)

        elif cs == self.status_complete:
            self.status_state = "complete"
            self.status_attributes["reason"] = "EV fully charged"
            self.status_attributes["charge_time_left"] = 0
            self.status_attributes["next_start"] = ""
            self.status_attributes["next_stop"] = ""
            self.status_attributes["slots"] = []
            self.update_status_entity()
            return

        if self.charge_time_needed is not None:
            self.status_attributes["charge_time_left"] = self.charge_time_needed

        self.start_stop_charging()

    def start_charging(self):
        self.call_service(
            "homeassistant/turn_on", entity_id=self.args["charger_switch"]
        )

    def stop_charging(self):
        self.call_service(
            "homeassistant/turn_off", entity_id=self.args["charger_switch"]
        )

    def start_stop_charging(self):
        if self.charge_time_needed is None:
            self.start_charging()
            self.log("Starting to charge to calculate time needed")
            self.status_state = "calculating"
            self.status_attributes["reason"] = "Asking EV for time left"
            self.status_attributes["charge_time_left"] = "unknown"
            self.status_attributes["next_start"] = ""
            self.status_attributes["next_stop"] = ""
            self.status_attributes["slots"] = []
            self.update_status_entity()
            return

        price = self.get_price()
        if price is None:
            self.log("We don't have required prices, aborting...")
            self.status_state = "stopped"
            self.status_attributes["reason"] = "Missing price info"
            self.status_attributes["next_start"] = ""
            self.status_attributes["next_stop"] = ""
            self.status_attributes["slots"] = []
            self.update_status_entity()
            self.stop_charging()
            return

        self.debug(f"We need {self.charge_time_needed} seconds to charge")

        price = sorted(price, key=lambda i: i["price"])

        self.debug(f"Valid prices: {len(price)}")

        # Find cheapest slots
        slots = []
        length = 0
        for p in price:
            slots.append(p)
            length += p["length"]

            if length > self.charge_time_needed:
                break

        self.status_attributes["slots"] = slots 

        if not len(slots):
            self.log("We don't need any slots...")
            self.status_state = "no slots"
            self.status_attributes["reason"] = "No slots needed"
            self.status_attributes["next_start"] = ""
            self.status_attributes["next_stop"] = ""
            self.update_status_entity()
            self.start_charging()
            return

        # Make sure we have the slots in time order
        slots = sorted(slots, key=lambda i: i["start"])

        self.debug(f"WE NEED THESE SLOTS: {slots} ({length})")

        now = self.get_now()
        slot = slots[0]

        # Try to find the next time charging will stop
        end_time = slot["start"]
        for s in slots:
            if s["start"] != end_time:
                break

            end_time = s["end"]

        self.status_attributes["next_start"] = slot["start"].ctime()
        self.status_attributes["next_stop"] = end_time.ctime()

        if slot["start"] < now < slot["end"]:
            self.start_charging()
            self.status_state = "charging"
            self.status_attributes["reason"] = "Inside of low rate time slot"
        else:
            self.stop_charging()
            self.status_state = "stopped"
            self.status_attributes["reason"] = "Outside of low rate time slot"

        self.update_status_entity()

    def convert_time_to_seconds(self, time_str):
        seconds = 0
        multipliers = [1, 60, 3600]

        for t in time_str.split(":"):
            seconds += int(t) * multipliers.pop()

        return seconds

    def get_config_value(self, param, default):
        if not param in self.args:
            return default

        v = self.get_entity_value(self.args[param])

        if isinstance(v, str):
            return v

        else:
            return self.args[param]

    def get_time_from_config(self, parameter, in_seconds=True):
        if not parameter in self.args:
            self.log(f"'{parameter}' not defined in config")
            return None

        time = self.get_state(self.args[parameter])
        if not time:
            time = self.args[parameter]

        return self.convert_time_to_seconds(time)

    def get_entity_and_attribute(self, name):
        s = name.split(",")

        if len(s) > 1:
            return s[0].strip(), s[1].strip()
        else:
            return s[0].strip(), None

    def get_price(self):
        if "price_data" not in self.args:
            return None

        now = self.get_now()
        # TODO, fix TZ
        midnight_today = datetime.now(tz.gettz("CET")).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # This is number of seconds from any midnight
        must_be_done_by = self.get_time_from_config("finish_at_latest_by")

        if (
            must_be_done_by is not None
            and (now - midnight_today).total_seconds() > must_be_done_by
        ):
            # add 24h to the must be done by (since we have passed the time
            # today already)
            must_be_done_by += 24 * 3600

        future_prices = []
        prices = []

        # merge all prices (today and tomorrow)
        for pd in self.args["price_data"]:
            e, a = self.get_entity_and_attribute(pd["entity"])
            part = self.get_state(entity_id=e, attribute=a)

            # Bail out if we miss data that is required
            if "required" in pd and pd["required"] and not len(part):
                self.log(
                    f"Missing required price info {pd['entity']}.{pd['attribute']}"
                )
                return None

            prices += part

        # Sort prices based on start time
        prices = sorted(prices, key=lambda i: i["start"])

        for p in prices:
            start = parser.parse(p["start"])
            end = parser.parse(p["end"])

            # If end time is in the past, skip it
            if (end - now).total_seconds() < 0:
                continue

            start_from_midnight_today = int(
                (start - midnight_today).total_seconds()
            )

            end_from_midnight_today = int(
                (end - midnight_today).total_seconds()
            )

            seconds_until_start = int((start - now).total_seconds())

            # If user say we bust have charged before a certain time
            # then don't include times later that that
            if (
                must_be_done_by is not None
                and start_from_midnight_today >= must_be_done_by
            ):
                # Start time is to late
                break

            # Calculate usable length based on time now and when we must be
            # done charging
            if (
                must_be_done_by is not None
                and end_from_midnight_today > must_be_done_by
            ):
                usable_length = must_be_done_by - start_from_midnight_today
            elif seconds_until_start < 0:
                usable_length = int((end - start).total_seconds())
                usable_length += seconds_until_start
            else:
                usable_length = int((end - start).total_seconds())

            # Store price info in our new parsed format
            future_prices.append(
                {
                    "start": start,
                    "end": end,
                    "start_from_midnight": start_from_midnight_today,
                    "end_from_midnight": end_from_midnight_today,
                    "length": usable_length,
                    "seconds_until_start": seconds_until_start,
                    "price": p["value"],
                }
            )

        return future_prices

    def update_status_entity(self):
        entity_id = "sensor." + self.name + "_status"

        self.set_state(
            entity=entity_id,
            state=self.status_state,
            attributes=self.status_attributes,
        )

