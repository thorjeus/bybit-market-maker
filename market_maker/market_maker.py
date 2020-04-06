# -*- coding: utf-8 -*-

'''
bybit-market-maker
------------------------
A very simple market maker bot that relies on the pybit module. Please note
that the bot has very little risk management features. This algorithm is NOT
equivalent to financial advice. Don't be an idiot; use at your own risk!

Documentation can be found at 
https://github.com/verata-veritatis/bybit-market-maker

:copyright: (c) 2020 verata-veritatis
:license: MIT License
'''

import time
from pybit import HTTP, WebSocket

# Import settings.py
from settings import *

if TICKER.endswith('USDT'):
    raise Exception('This program currently isn\'t compatible with USDT '
        'pairs. Please use a USD pair or check back at a later time.')

if NUM_OF_ORDERS > 50:
    raise Exception('Too many orders. Please lower your number'
        ' of orders to 50 or below.')

class Requests:

    def __init__(self):

        if TEST_NET:
            http_endpoint = 'https://api-testnet.bybit.com'
            ws_endpoint = 'wss://stream-testnet.bybit.com/realtime'
        else:
            http_endpoint = 'https://api.bybit.com'
            ws_endpoint = 'wss://stream.bybit.com/realtime'

        self.session = HTTP(endpoint=http_endpoint, api_key=API_KEY, 
            api_secret=PRIVATE_KEY)
        self.ws = WebSocket(endpoint=ws_endpoint, api_key=API_KEY,
            api_secret=PRIVATE_KEY, 
            subscriptions=[f'instrument_info.100ms.{TICKER}', 'position', 
                'order'])

    def place_initial_orders(self, last_price, prices, quantity):
        responses = []
        self.set_to_cross()
        for price in prices:
            if price > last_price:
                side = 'Sell'
            else:
                side = 'Buy'
            responses.append(self.session.place_active_order(
                symbol=TICKER,
                order_type='Limit',
                side=side,
                qty=quantity,
                price=price,
                time_in_force='PostOnly',
            ))
        return responses

    def place_closing_orders(self, side, prices, quantity):
        responses = []
        for price in prices:
            responses.append(self.session.place_active_order(
                symbol=TICKER,
                order_type='Limit',
                side=side,
                qty=quantity,
                price=price,
                time_in_force='PostOnly',
                reduce_only=True
            ))
        return responses

    def close_position(self):
        return self.session.close_position(TICKER)

    def cancel_all(self):
        return self.session.cancel_all_active_orders(TICKER)

    def get_wallet_balance(self):
        r = self.session.get_wallet_balance('BTC')
        return r['result'][TICKER[:3]]['available_balance']

    def get_position(self):
        return self.ws.fetch('position')

    def get_last_price(self):
        instr = self.ws.fetch(f'instrument_info.100ms.{TICKER}')
        return instr['last_price_e4'] * 10**-4

    def ping(self):
        return self.ws.ping()

    def set_to_cross(self):
        return self.session.change_user_leverage(TICKER, 0)

    def set_stop_loss(self):
        self.session.set_trading_stop(TICKER, 
            stop_loss=self.get_position()[TICKER]['entry_price'])

    def _test_sub(self):
        return self.ws.fetch(f'instrument_info.100ms.{TICKER}')

class Algorithm:

    def __init__(self):
        self.req = Requests()

    def submit_initial(self):

        # Determine last price.
        last_price = self.req.get_last_price()

        # Set the minimum and maximum of the range.
        max_p = last_price + SPREAD/2
        min_p = last_price - SPREAD/2

        # Determine the interval.
        interval = (max_p - min_p)/(NUM_OF_ORDERS - 1)

        # Scale the prices.
        prices = [max_p - interval*i for i in range(NUM_OF_ORDERS - 1)]
        prices.append(min_p)

        # Determine the margin per order.
        balance = self.req.get_wallet_balance() * last_price * MARGIN
        quantity = balance/NUM_OF_ORDERS

        # Set initial orders.
        self.req.place_initial_orders(last_price, prices, quantity)

        # Return last price.
        return last_price, interval, quantity

    def submit_closing(self, median, interval, qty):

        p_req = self.req.get_position()
        if p_req != []:
            position = p_req[TICKER]['size']
        else:
            self.req.cancel_all(); self.req.close_position()
            raise Exception('Can\'t obtain position size.')

        if p_req[TICKER]['side'] == 'Buy':
            side = 'Sell'
        elif p_req[TICKER]['side'] == 'Sell':
            side = 'Buy'
            interval = -interval

        num_filled = round(position/qty)

        prices = [median + interval*(i+1) for i in range(num_filled)]

        return self.req.place_closing_orders(side, prices, qty)

    def run(self):
        
        # Close any initial position.
        try:
            if self.req.get_position()[TICKER]['size'] > 0:
                self.req.close_position()
        except KeyError:
            pass

        # Set initial booleans.
        orders_set = False
        closing = False

        # Await connection.
        while self.req._test_sub() == {}:
            time.sleep(1)

        while True:

            # Reset booleans
            if closing:
                orders_set = False
                closing = False

            # Place initial orders.
            if not orders_set:
                median, interval, qty = self.submit_initial()
                set_time = time.time()
                orders_set = True

            # Wait for position.
            position = self.req.get_position()

            # If we haven't received any position data yet, handle it.
            try:
                position[TICKER]['size']
            except KeyError:
                position = {}; position[TICKER]['size'] = 0

            # While we are in a position...
            while position[TICKER]['size'] > 0:

                # If we're not trying to close yet.
                if not closing:

                    # Reload last price.
                    last = self.req.get_last_price()

                    # If we're in position and we cross back over the median.
                    if ((position[TICKER]['side'] == 'Buy' and last > median) or 
                        (position[TICKER]['side'] == 'Sell' and last < median)):

                        # Cancel all orders and set stop loss at B.E.
                        self.req.cancel_all()
                        self.req.set_stop_loss()

                        # Set close orders and set boolean to True.
                        self.submit_closing(median, interval, qty)
                        closing = True

                # Reload position.
                position = self.req.get_position()

                # Sleep for a second.
                time.sleep(1)

            # If we're waited until reset time without fills, retry.
            if (time.time() - set_time > ORDER_RESET_TIME and not closing and 
                orders_set and position[TICKER]['size'] == 0):

                # Cancel and retry.
                self.req.cancel_all()
                orders_set = False

            # Sleep for three seconds.
            time.sleep(3)

class Application:
    def __init__(self):
        Algorithm().run()
