Smart EV Charging
=================

_Charge your Electric Vehicle when the utility rate is the lowest_

Currently only Tesla is supported.

This [AppDaemon](https://appdaemon.readthedocs.io/en/latest/#) app for [Home Assistant](https://www.home-assistant.io/) require a sensor to get the hourly rate. Currently the [Nordpool](https://github.com/custom-components/nordpool) Custom Component is supported. It also requires the [Tesla Integration](https://www.home-assistant.io/integrations/tesla/) to work.

[![buy-me-a-coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/EvTheFuture)

## Quick Example

This is an example configuration that will make sure charging is completed by 07:30 in the morning.
Please Note: You need to change the entities to match your setup.
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

