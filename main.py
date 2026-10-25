import gpxpy
import gpxpy.gpx
import pandas as pd
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from plotly.subplots import make_subplots


gpx_file = open('input2.gpx', 'r')

gpx = gpxpy.parse(gpx_file)

rows = []

for track_idx, track in enumerate(gpx.tracks):
    for segment_idx, segment in enumerate(track.segments):
        for point_idx, point in enumerate(segment.points):

            rows.append({
                "track": track_idx,
                "segment": segment_idx,
                "point": point_idx,

                "latitude": point.latitude,
                "longitude": point.longitude,
                "elevation": point.elevation,
                "time": point.time,

                "speed": point.speed,
                "course": point.course,

                "name": point.name,
                "comment": point.comment,
                "description": point.description,
            })

df = pd.DataFrame(rows)

print(df.head())
print(df.info())


def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """Вычисление расстояния между географическими координатами в метрах (Haversine formula)"""
    R = 6371000.0  # Радиус Земли в метрах

    lat1_rad, lon1_rad = np.radians(lat1), np.radians(lon1)
    lat2_rad, lon2_rad = np.radians(lat2), np.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2.0) ** 2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

    return R * c


def process_gpx_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 1. Сглаживание высоты (Rolling Mean) для устранения шума GPS
    df['elevation_smooth'] = df['elevation'].rolling(window=5, min_periods=1, center=True).mean()

    # 2. Смещение координат для расчёта дистанции с предыдущей точкой
    df['lat_prev'] = df['latitude'].shift(1)
    df['lon_prev'] = df['longitude'].shift(1)
    df['ele_prev'] = df['elevation_smooth'].shift(1)

    # 3. Расчёт расстояния (в метрах)
    df['delta_distance'] = calculate_haversine_distance(
        df['latitude'], df['longitude'],
        df['lat_prev'], df['lon_prev']
    ).fillna(0.0)

    # 4. Расчёт перепада высоты (в метрах)
    df['delta_elevation'] = (df['elevation_smooth'] - df['ele_prev']).fillna(0.0)

    # 5. Расчёт кумулятивной дистанции (км)
    df['cum_distance_km'] = df['delta_distance'].cumsum() / 1000.0

    # 6. Расчёт уклона (Slope %)
    # Защита от деления на ноль при совпадении точек
    df['slope_percent'] = np.where(
        df['delta_distance'] > 0.5,
        (df['delta_elevation'] / df['delta_distance']) * 100.0,
        0.0
    )

    # Ограничиваем нереалистичные выбросы уклона [-45%, +45%]
    df['slope_percent'] = df['slope_percent'].clip(-45.0, 45.0)

    # Удаляем временные столбцы
    drop_cols = ['lat_prev', 'lon_prev', 'ele_prev', 'time', 'speed', 'course', 'name', 'comment', 'description']
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    return df

# Применение к вашему датафрейму:
df_processed = process_gpx_dataframe(df)

print(df_processed.head())
print(df_processed.info())


def extract_track_summary(df_processed: pd.DataFrame) -> dict:
    total_distance_km = df_processed['delta_distance'].sum() / 1000.0

    # Разделение на подъемы и спуски
    elevation_gains = df_processed[df_processed['delta_elevation'] > 0]['delta_elevation'].sum()
    elevation_losses = np.abs(df_processed[df_processed['delta_elevation'] < 0]['delta_elevation'].sum())

    # Распределение уклонов (% дистанции с крутым подъёмом > 10%)
    steep_climb_distance = df_processed[df_processed['slope_percent'] > 10]['delta_distance'].sum() / 1000.0
    steep_climb_ratio = (steep_climb_distance / total_distance_km) if total_distance_km > 0 else 0

    return {
        'total_distance_km': round(total_distance_km, 2),
        'elevation_gain_m': round(elevation_gains, 1),
        'elevation_loss_m': round(elevation_losses, 1),
        'avg_elevation_m': round(df_processed['elevation_smooth'].mean(), 1),
        'max_elevation_m': round(df_processed['elevation_smooth'].max(), 1),
        'min_elevation_m': round(df_processed['elevation_smooth'].min(), 1),
        'max_slope_percent': round(df_processed['slope_percent'].max(), 1),
        'steep_climb_ratio': round(steep_climb_ratio, 3)
    }




def plot_interactive_elevation_profile(df_processed: pd.DataFrame):
    """
    Строит двухосный интерактивный график профиля высоты и уклонов.
    """
    # Создаем субплот с двумя независимыми Y-осями
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # 1. Заливка под профилем высоты (Элегантно выглядит с прозрачностью)
    fig.add_trace(
        go.Scatter(
            x=df_processed['cum_distance_km'],
            y=df_processed['elevation_smooth'],
            name="Высота (м)",
            mode='lines',
            line=dict(color='#2B5C8F', width=2),
            fill='tozeroy',
            fillcolor='rgba(43, 92, 143, 0.15)',
            hovertemplate=(
                "<b>Дистанция:</b> %{x:.2f} км<br>" +
                "<b>Высота:</b> %{y:.1f} м<br>"
            )
        ),
        secondary_y=False,
    )

    # 2. График уклона (%) с цветовым кодированием
    # Для информативности применим сглаживание к уклону
    slope_smooth = df_processed['slope_percent'].rolling(window=3, min_periods=1, center=True).mean()

    fig.add_trace(
        go.Scatter(
            x=df_processed['cum_distance_km'],
            y=slope_smooth,
            name="Уклон (%)",
            mode='lines',
            line=dict(color='#E76F51', width=1.5, dash='dot'),
            hovertemplate="<b>Уклон:</b> %{y:.1f}%<br>",
            opacity=0.8
        ),
        secondary_y=True,
    )

    # 3. Добавление нулевой линии для уклона (разделитель подъем/спуск)
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="gray",
        opacity=0.5,
        secondary_y=True
    )

    # Настройка осей и оформления
    fig.update_layout(
        title=dict(
            text="<b>Профиль высоты и крутизны маршрута</b>",
            font=dict(size=20, family="Arial")
        ),
        template="plotly_white",
        hovermode="x unified",  # Синхронный курсор для обеих осей
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        margin=dict(l=60, r=60, t=80, b=50),
        height=500
    )

    # Настройка подписей осей
    fig.update_xaxes(
        title_text="<b>Дистанция (км)</b>",
        showgrid=True,
        gridcolor='#E5E5E5'
    )
    fig.update_yaxes(
        title_text="<b>Высота (м)</b>",
        secondary_y=False,
        showgrid=True,
        gridcolor='#E5E5E5'
    )
    fig.update_yaxes(
        title_text="<b>Уклон (%)</b>",
        secondary_y=True,
        showgrid=False
    )

    # Отображение (в браузер или Jupyter)
    fig.show()

    # Опционально: сохранение в HTML-файл для просмотра без Python
    # fig.write_html("elevation_profile.html")

plot_interactive_elevation_profile(df_processed)