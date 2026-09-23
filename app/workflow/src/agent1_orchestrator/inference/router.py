ROUTES = {
    "Body": "body",
    "Lead": "lead",
    "Text": "text",
}

def route_feature(predicted_feature):
    return ROUTES.get(predicted_feature)
