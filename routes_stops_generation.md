# Route
## Fields

- fid
- route_id                  : permanent unique id. commonly it's an abbreviation, with
prefix the operator
- service_id                : identifies both direction as a service. sarbagita / tmd / intaran
- direction_id              : 0 / 1
- type                      : route
- to                        : <end stop>
- route                     : bus
- ref                       : <the route name>
- public_transport:version  : 2
- operator                  : <operator>
- name                      : <start stop> → <end stop>
- from                      : <start stop>
- colour                    : <hex of line>
- source                    : `osm` for official / `manual` for manual entry.
mostly will be filled as manual
- osm_relation_id           : point back to OSM if exists

## Example

- fid
- route_id                  : sarbagita_gwk_gor
- service_id                : sarbagita
- direction_id              : 0
- type                      : route
- to                        : GOR Ngurah Rai
- route                     : bus
- ref                       : TS1
- public_transport:version  : 2
- operator                  : Trans Sarbagita
- name                      : Garuda Wisnu Kencana → GOR Ngurah Rai
- from                      : Garuda Wisnu Kencana
- colour                    : #123456
- source                    : osm / manual / official / surveyed
- osm_relation_id           : point back to OSM if exists


# Stops Fields
## Fields
- fid
- stop_id               : STOP_<line name>_<start point id>_<order of this in that route>
- name                  :
- name:en               : 
- route_ref             : <line name?
- public_transport      : platform
- bus                   : yes
- operator              : <operator>
- direction             : <0 or 1>
- shelter
- bench
- lit
- bin
- amenity
- highway               : bus_stop
- access
- kerb

## Example

- fid
- stop_id           : STOP_TS1_0_05
- name              : Abian Base
- name_en           : Abian Base
- route_ref         : K1B
- public_transport  : platform
- bus               : yes
- operator          : Trans Metro Dewata
- direction         : 1
- shelter           : null
- bench             : null
- lit               : null
- bin               : null
- amenity           : null
- highway           : bus_stop
- access            : null
- kerb              : null