"""Mongo index manifest (idempotent). Run at app startup.
Indexes assume LEON multi-tenant design (hotel_name first when applicable).
"""
import os
import pymongo


def ensure_mongo_indexes():
    mongo_url = os.getenv('MONGO_URL')
    if not mongo_url:
        return "Mongo URL missing; skipped index ensure."
    client = pymongo.MongoClient(mongo_url)
    db = client.get_database()

    # maintenance_progress
    col = db.get_collection('maintenance_progress')
    col.create_index([('task_id', pymongo.ASCENDING)], background=True)
    col.create_index([('hotel_name', pymongo.ASCENDING), ('task_id', pymongo.ASCENDING)], background=True)
    col.create_index([('updated_at', pymongo.DESCENDING)], background=True)

    # room_encyclopedia
    col = db.get_collection('room_encyclopedia')
    col.create_index([('hotel_name', pymongo.ASCENDING), ('room_type', pymongo.ASCENDING)], background=True)
    col.create_index([('hotel_name', pymongo.ASCENDING)], background=True)

    # guest_bookings
    col = db.get_collection('guest_bookings')
    col.create_index([('hotel_name', pymongo.ASCENDING), ('booking_id', pymongo.ASCENDING)], background=True)
    col.create_index([('guest_phone', pymongo.ASCENDING)], background=True)
    col.create_index([('created_timestamp', pymongo.DESCENDING)], background=True)

    # booking_policies
    col = db.get_collection('booking_policies')
    col.create_index([('hotel_name', pymongo.ASCENDING), ('policy_type', pymongo.ASCENDING), ('status', pymongo.ASCENDING)], background=True)

    return "Mongo indexes ensured."


if __name__ == '__main__':
    print(ensure_mongo_indexes())


