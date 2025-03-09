from flask import jsonify, request
import logging
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

logger = logging.getLogger(__name__)


def create_order():
    """Create a new order with proper validation, transaction handling, and inventory control."""
    try:
        # Input validation
        order_data = request.get_json()
        if not order_data:
            raise BadRequest("Missing order data")

        # Validate required fields
        required_fields = ['productId', 'quantity', 'userId']
        if not all(field in order_data for field in required_fields):
            raise BadRequest(f"Missing required fields: {required_fields}")

        product_id = order_data.get('productId')
        quantity = order_data.get('quantity', 1)

        # Start database transaction
        with db.transaction():
            # Check inventory availability first
            product = db.query(
                "SELECT id, stock, price FROM products WHERE id = %s FOR UPDATE",
                (product_id,)
            ).fetchone()

            if not product:
                raise BadRequest(f"Product with ID {product_id} not found")

            if product['stock'] < quantity:
                raise BadRequest(
                    f"Insufficient stock for product {product_id}. Available: {product['stock']}, Requested: {quantity}")

            # Generate order with additional data
            order_id = generate_unique_id()
            total_price = product['price'] * quantity

            # Create order record
            db.insert(
                'orders',
                {
                    'id': order_id,
                    'user_id': order_data['userId'],
                    'product_id': product_id,
                    'quantity': quantity,
                    'total_price': total_price,
                    'status': 'created',
                    'created_at': db.now()
                }
            )

            # Update inventory atomically
            rows_updated = db.execute(
                "UPDATE products SET stock = stock - %s WHERE id = %s AND stock >= %s",
                (quantity, product_id, quantity)
            )

            if rows_updated == 0:
                # This provides an extra safety check in case of race conditions
                raise BadRequest(
                    "Inventory update failed - stock may have changed")

            # Commit happens at the end of the with block

        # Publish event for order creation (outside transaction)
        publish_event('order.created', {
            'order_id': order_id,
            'product_id': product_id,
            'quantity': quantity
        })

        return jsonify({
            "orderId": order_id,
            "status": "created",
            "message": "Order successfully created"
        }), 201

    except BadRequest as e:
        # Client errors (4xx)
        logger.info(f"Invalid order request: {str(e)}")
        return jsonify({"error": str(e)}), 400

    except SQLAlchemyError as e:
        # Database errors
        logger.error(f"Database error during order creation: {str(e)}")
        return jsonify({"error": "Database error occurred during order processing"}), 500

    except Exception as e:
        # Unexpected errors
        logger.exception(f"Unexpected error during order creation: {str(e)}")
        return jsonify({"error": "Order processing failed"}), 500
