#pragma once
#include <QComboBox>
#include <QPointer>
#include <QJsonObject>
#include <functional>

namespace gc {
class ArraySelection final : public QObject {
public:
    ArraySelection(QObject *property, QObject *editor, QComboBox *combo,
        QString build, std::function<void(QJsonObject)> log, bool routeSignals = true);
    void setWorld(QString value) { world = std::move(value); }
    bool isBusy() const { return busy; }
    void commit(const QString &value);
private:
    QPointer<QObject> property;
    QPointer<QComboBox> combo;
    QString build, world;
    std::function<void(QJsonObject)> log;
    bool busy = false;
};
}
