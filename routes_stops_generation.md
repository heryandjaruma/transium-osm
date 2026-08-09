# Route Fields

- fid
- route_id          : permanent unique id
- service_id        : identifies both direction as a service
- direction_id      : 0 / 1
- type              : route
- to
- route             : bus
- ref               : TS1
- public_transport:version  : 2
- operator                  : Trans Sarbagita
- name
- from
- colour
- source            : osm / manual / official / surveyed
- osm_relation_id   : point back to OSM if exists

# Stops Fields

- fid
- stop_id               : STOP_TS1_0_05
- name                  :
- name:en               : 
- route_ref             : TS1
- public_transport      : platform
- bus                   : yes
- operator              : Trans Sarbagita
- direction             : the destination id
- shelter
- bench
- lit
- bin
- amenity
- highway               : bus_stop
- access
- kerb


# Direction ID

Route is defined by its Transit Name Sorting. Then in filename, starting point is its own direction.

0 GOR Ngurah Rai Dalam
1 Garuda Wisnu Kencana