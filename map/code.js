class LoadingProgress {
  constructor(id) {
    this._ele = document.getElementById(id);
  }

  progress(text) {
    this._ele.innerHTML += '<br>' + text;
  }

  done() {
    this._ele.remove();
  }
}


// TODO remove me as a global
let points = null;


class Walking {
  constructor(elementId) {
    this._map = L.map(elementId).setView([48.165, 11.57], 12);

    L.tileLayer(
      'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 
      {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }
    ).addTo(this._map);

    this._tracks = [];
  }

  _sort() {
    this._tracks.sort((a,b) => {
      //const start_a = a.getStartTime();
      //const start_b = b.getStartTime();

      //return start_a - start_b;

      const start_a = a.getStartTime();
      const start_b = b.getStartTime();
       if (start_a === undefined) {
            return -1;
        }
        if (start_b === undefined) {
            return 1;
        }

      return start_a - start_b;

    });
  }

  _aggregateByDay() {

  }

  count() {
    return this._tracks.length;
  }

  setPosition(value, mode, outdistance) {
    let distance = 0;
    for (i = 0; i < this._tracks.length; ++i) {
        if ((mode === "0" &&  i <= value) || i == value) {
          this._tracks[i].addTo(this._map);
          distance += this._tracks[i].getDistance();
        } else {
          this._tracks[i].remove();
        }
      }
      // TODO: make this some form of event
      outdistance.innerText = Math.round(distance / 1000);
    }

  setData(fullcollection, progress) {
    if (fullcollection.type != "FeatureCollection") {
      alert("Expected data to be of type 'FeatureCollection'");
    }

    progress.progress('... Daten geladen');

     // TODO move this elsewhere
    pointToTime = (function() { {};
      const data = {};
      fullcollection.features.filter(feature => {
        return feature.geometry.type == "Point";
      }).forEach(point => {
        const coords = point.geometry.coordinates;
        const gpxTime = point.properties["gpx:time"];
        data[coords[0]+','+coords[1]] = gpxTime ? Date.parse(gpxTime) : null;
      });
      points = data;
      return (point) => data[point[0]+','+point[1]];
    })();

    progress.progress('... Point-Index generiert');

    this._tracks = fullcollection.features.filter(feature => {
      return feature.geometry.type == "LineString";
    }).map(track => (new Track(this, track, { color: 'red', weight: 2 })));

    progress.progress('... Tracks generiert');

    this._sort();
    let i = 0;
    this._tracks.forEach(t => t.id = i++)

    progress.progress('... Tracks sortiert');
  }

  debugTrack(id) {
    let sum = 0;
    const layers = [];
    this._tracks[id]._data.geometry.coordinates.forEach((c, i) => {
      layers.push(L.circleMarker([c[1], c[0]], { radius: 1}).addTo(this._map));
      if (i) {
        const distance = Math.round(this._map.distance(this._tracks[id]._data.geometry.coordinates[i-1], c));
       sum += distance;
       console.log(distance, sum);
       layers.push(L.polyline([ [c[1], c[0]], [this._tracks[id]._data.geometry.coordinates[i-1][1], this._tracks[id]._data.geometry.coordinates[i-1][0]  ]], { color:"red", weight:2 }  ).addTo(this._map).bindTooltip("t="+points[c[0]+','+c[1]]+"; d="+ distance));
      }
    })
    console.log(this._tracks[id], this._tracks[id].getDistance());
    return {
      clear() {
        layers.forEach(layer => layer.remove());
      }
    };
  }
}


// TODO: inject into Track objects instead of global
let pointtoTime = null;

const Track  = L.Polyline.extend({
  initialize(walking, data, options) {
    L.Util.setOptions(this, options);
    const coordinates = data.geometry.coordinates;
    this._setLatLngs(coordinates.map(coord => [coord[1], coord[0]]));
    this._data = data;
    this._calcDistance(walking);
    this.bindPopup(() => {
      const time = pointToTime(coordinates[0])
      const start = time ? new Date(time) : '(unbekannt)';
      const distance = this.getDistance();
      return `ID: ${this.id} <br>Start: ${start}<br>Distance: ${distance}m`;
    });
  },
	
  getStartTime() {
    return pointToTime(this._data.geometry.coordinates[0]);
  },
  _calcDistance(walking) {
    this._distance = 0;
    const coordinates = this._data.geometry.coordinates;
    for (i = 1; i < coordinates.length; ++i) {
      this._distance += walking._map.distance(coordinates[i-1], coordinates[i]);
    }
  },
  getDistance() {
    return this._distance;
  }
})



