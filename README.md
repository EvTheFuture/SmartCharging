Smart EV Charging
=================

_Charge your Electric Vehicle when the utility rate is the lowest_

Currently only Tesla is supported.

This [AppDaemon](https://appdaemon.readthedocs.io/en/latest/#) app for [Home Assistant](https://www.home-assistant.io/) require a sensor to get the hourly rate. Currently the [Nordpool](https://github.com/custom-components/nordpool) Custom Component is supported. It also requires the [Tesla Integration](https://www.home-assistant.io/integrations/tesla/) to work.

[![buy-me-a-coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/EvTheFuture)

## Screenshots
<p align="center" width="100%">
    <img src="https://user-images.githubusercontent.com/66333723/102418686-86c17300-3ffe-11eb-80fb-7e076810bd2f.jpg" width=240 alt="Screenshot">
    <img src="https://user-images.githubusercontent.com/66333723/102502005-60431c80-407e-11eb-8ab1-75537108f085.png" width=240 alt="Screenshot">
    <img src="https://user-images.githubusercontent.com/66333723/102501910-40abf400-407e-11eb-889c-52868a501177.png" width=240 alt="Screenshot">
</p>

## Quick Example

This is an example configuration that will make sure charging is completed by 07:30 in the morning. You can also enter an entity I'd of an input datetime (time only) entity in order to be able to easily change the time from Lovelace GUI

**Please Note:** You need to change the entities to match your setup.
```
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
    start_by_the_latest_at: "03:00"
    debug: no
```

